# Isolated Completion Verification in Harbor 0.23.0

Date: 2026-09-27

## Authoritative conclusion

Harbor 0.23.0 does **not** expose a provider-neutral API for snapshotting,
cloning, checkpointing, pausing, or committing a live task environment. Its
portable contract provides environment lifecycle, command execution, and
host-to-environment file or directory transfer. Therefore, the strongest
provider-neutral isolation available for a potentially write-producing
completion check is:

1. create a disposable environment from the same resolved task environment
   configuration at the orchestration layer;
2. copy the required filesystem state out of the scored environment with
   `download_file` or `download_dir`;
3. materialize it at the same discovered absolute path in the disposable
   environment with `upload_file` or `upload_dir`;
4. run the check there; and
5. stop and delete the disposable environment on every exit path.

This is a **file-state reconstruction**, not a live-environment snapshot. It
cannot faithfully preserve running processes, process memory, open sockets,
namespaces, sidecar state, mounted-volume contents not explicitly copied,
kernel state, or changes outside the selected paths. A check that requires
those states cannot be both faithful and non-mutating through a
provider-neutral mechanism. It must be rewritten as an observation-only check,
given an explicit restartable fixture, or handled by a provider-specific
runtime implementation.

Docker `commit` followed by a fresh `docker create`/`start` is useful only as an
optional Docker optimization. It is not provider-neutral, omits mounted-volume
data, does not preserve processes, does not clone Compose sidecars, transiently
pauses the source container by default, and requires explicit reconstruction
and cleanup of runtime settings. It is not a general replacement for the
portable file-transfer design.

## Scope and source pinning

The project pins `harbor==0.23.0` in
[`pyproject.toml`](../pyproject.toml) and resolves version 0.23.0 in
[`uv.lock`](../uv.lock). The official `v0.23.0` tag dereferences to Harbor
commit
[`1e5c5c6db929a10a140d05e606882c671ae20729`](https://github.com/laude-institute/harbor/tree/1e5c5c6db929a10a140d05e606882c671ae20729).
The installed `base.py`, `trial.py`, `docker.py`, and `artifact_handler.py`
matched the corresponding files at that commit byte-for-byte during this
inspection.

The evaluated dataset is `terminal-bench@2.0`. Trial records identify its
official source as
[`laude-institute/terminal-bench-2`](https://github.com/laude-institute/terminal-bench-2/tree/69671fbaac6d67a7ef0dfec016cc38a64ef7a77c)
at commit `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`. The task census below was
performed over all 89 task trees in Harbor's local immutable task cache for
that revision.

## What Harbor actually supports

### Provider-neutral environment contract

`BaseEnvironment` defines these relevant portable operations:

- `start(force_build)`
- `stop(delete)`
- `exec(command, cwd, env, timeout_sec, user)`
- `upload_file` and `upload_dir`
- `download_file` and `download_dir`

See Harbor's
[`BaseEnvironment` lifecycle and transfer methods](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/base.py#L908-L963)
and
[`exec` contract](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/base.py#L1135-L1155).
There is no `snapshot`, `clone`, `checkpoint`, `commit`, `pause`, `resume`, or
environment-to-environment copy method. Snapshot support is also absent from
the
[`EnvironmentCapabilities` model](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/capabilities.py#L12-L63).

The contract does include service-oriented methods, but sidecar operations are
capability-gated. The base implementation routes the main service to ordinary
environment operations and rejects sidecar operations; Compose-capable
providers must override them. See
[`BaseEnvironment` service operations](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/base.py#L1185-L1315).
Consequently, even service control is not a universal environment-cloning
primitive.

Harbor already uses tar internally for filtered directory downloads. That code
creates an archive inside the source environment, downloads and extracts it on
the host, and removes the temporary archive. This is a transfer implementation,
not an atomic environment snapshot. See
[`download_dir_with_exclusions`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/base.py#L965-L1034).

### Shared and separate verifier environments

Harbor supports two verifier modes:

- **Shared**: the verifier runs in the agent's environment.
- **Separate**: Harbor creates a fresh verifier environment and uploads
  declared artifacts into it.

The mode defaults to shared. Supplying a verifier environment implies separate
mode, and an explicit separate mode without a distinct configuration receives a
deep copy of the task environment configuration. See
[`verifier_mode.py`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/models/task/verifier_mode.py#L10-L64).

In a single-step trial, Harbor runs the agent, collects artifacts, stops the
agent environment before separate verification, and otherwise runs shared
verification before stopping it. See
[`SingleStepTrial._run`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/single_step.py#L38-L55).
The separate-verifier path creates a target environment and uploads artifacts;
it does not clone the agent filesystem. See
[`Trial._run_separate_verifier`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/trial.py#L787-L837).

Artifact rematerialization is intentionally path-based. Unless the artifact is
the conventional artifact directory, Harbor uploads each entry back to its
original `source` path. It creates parent directories but transfers only the
declared entries. See
[`ArtifactHandler.upload_artifacts`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/artifact_handler.py#L169-L213).

This existing separate-verifier facility is the closest supported Harbor
feature to isolated verification. It is suitable only when task authors declare
all verifier inputs as artifacts, or when orchestration is extended to transfer
the required workspace explicitly.

### Verifiers can write

Harbor uploads tests to `/tests`, may make test scripts executable, executes
them, and directs verifier output into `/logs/verifier`. Nothing in the
environment contract prevents a test script from also changing the workspace,
installing packages, or starting processes. See
[`Verifier.verify`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/verifier/verifier.py#L165-L232).

Before the isolation change, Evidence Harness turned completion checks into
ordinary commands in the live environment. Its policy rejected commands that
syntactically appeared to mutate state, but text inspection could not prove
runtime purity. Compilers, package managers, test runners, imported code, shell
functions, and subprocesses can write without an obvious redirection or
mutating command token.

## Terminal-Bench 2.0 environment facts

The 89 pinned tasks have these properties:

| Property | Count |
|---|---:|
| Task configurations | 89 |
| `docker_image` configured | 89 |
| Explicit `task.toml` workdir | 0 |
| Explicit verifier mode | 0 |
| Declared artifacts | 0 |
| Step-based tasks | 0 |
| Task `docker-compose.yaml` files | 0 |
| Task healthcheck declarations | 0 |

Because no task selects a verifier mode, Harbor 0.23.0 resolves every task to
shared verification. Because no task declares artifacts, switching to separate
mode alone would not reproduce the modified workspace.

The final Dockerfile `WORKDIR` census is:

| Final `WORKDIR` | Count | Exceptions |
|---|---:|---|
| `/app` | 86 | |
| `/app/personal-site` | 1 | `fix-git` |
| `/app/dclm` | 1 | `sanitize-git-repo` |
| `/workspace` | 1 | `prove-plus-comm` |

Thus `/app` is a dataset convention, not a Harbor contract, and `/workspace`
is present in the pinned task set. The nested `/app` workdirs also show that
even selecting `/app` as a universal check root is imprecise.

Harbor's own stable Linux paths are `/logs`, `/tests`, `/solution`, and
`/harbor/skills`; `/app` and `/workspace` are not among them. See
[`EnvironmentPaths`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/models/trial/paths.py#L11-L48).
When no workdir is configured, Harbor itself asks the environment for `pwd`
before uploading task environment content, rather than assuming `/app`; see
[`BaseEnvironment._upload_environment_dir_after_start`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/base.py#L900-L906).

An isolation implementation should therefore discover the effective workdir
from resolved environment metadata or `pwd`, then rematerialize the copied
state at that same absolute path in the disposable environment. Merely setting
`cwd` to a scratch copy is insufficient when a check or tested program uses
absolute `/app` or `/workspace` paths.

## Supported APIs versus techniques

| Mechanism | Category | Protects scored workspace from check writes? | Fidelity | Portability and verdict |
|---|---|---|---|---|
| Harbor separate verifier plus declared artifacts | Supported Harbor orchestration | Yes | Declared files and directories only | Provider-neutral and preferred when task configuration can declare complete inputs |
| Fresh `BaseEnvironment` plus `download_dir`/`upload_dir` | Composition of supported Harbor APIs | Yes | Explicitly copied paths only | Strongest provider-neutral design for completion checks; requires orchestration support because an agent receives one environment, not an environment factory |
| Copy workspace to a scratch directory in the same environment | Shell technique | No general guarantee; only for checks proven to remain inside the copy | Workspace files only | Useful narrow fallback; absolute paths, escaping links, and writes outside the copy can still hit scored state |
| `tar` copy | Shell technique | Only when extracted into a separately confined environment | Selected files, metadata depending on flags and privileges | Broadly available but not guaranteed; non-atomic on a live tree and not runtime state |
| `cp -a` | Shell technique | Only when the destination is separately confined and links cannot escape | Selected files with best-effort attribute preservation | Depends on Unix userland and free space; not atomic or provider-neutral |
| `git worktree` | Git technique | No general guarantee; linked worktrees share repository data and refs | A checkout of Git state, not an arbitrary dirty filesystem snapshot | Inapplicable to non-Git tasks and incomplete for untracked, ignored, generated, installed, or out-of-tree state |
| OverlayFS lower/upper/work mount | Linux kernel technique | Only with a mount namespace that forces all accesses through the merged path | Copy-on-write filesystem view only | Requires Linux OverlayFS and mount privileges; not a Harbor capability and commonly unavailable inside task containers |
| `docker cp` | Docker engine technique | Yes when copying into another container or host path | Files and directories only | Docker-specific; Harbor's Docker provider uses it as a transfer backend, not a snapshot |
| `docker commit` plus fresh container | Docker engine technique | Check writes are isolated in the fresh container; commit itself pauses source by default | Main container writable layer and image configuration, excluding mounted data and process state | Optional Docker-only path with substantial lifecycle and fidelity gaps; not the portable default |

### `tar` and `cp -a`

GNU `cp -a` recursively copies and attempts to preserve links and file
attributes, but some ownership, ACL, security-context, and extended-attribute
preservation depends on privilege and filesystem support. Copying special files
can be unsafe or block indefinitely. See the
[`cp` manual](https://www.gnu.org/software/coreutils/manual/html_node/cp-invocation.html).

A tar stream is often a better transport across an environment boundary, and
Harbor itself uses one for some directory transfers. Neither tar nor `cp -a`
provides an atomic view of a directory while other processes are modifying it.
Neither captures processes, mounts, sockets, kernel objects, package changes
outside the copied tree, or service state.

### Git worktrees

`git worktree add --detach` creates another checkout attached to the same
repository and commit. Git documents linked worktrees as sharing repository
data while retaining per-worktree state. It does not describe or provide a
snapshot of arbitrary current filesystem state. See the official
[`git-worktree` documentation](https://git-scm.com/docs/git-worktree).

For completion verification, a new worktree starts from a commit rather than
automatically reproducing all dirty tracked changes, untracked outputs,
ignored build products, nested repositories, system installations, or files
outside the repository. Linked worktrees also share repository data and most
refs, so Git operations in the check can affect shared state. Reconstructing
dirty state separately and sandboxing shared metadata reduces this approach to
another file-copy scheme.

### OverlayFS

OverlayFS can expose a lower directory through a merged mount while sending
writes and deletions to an upper directory. The kernel documentation defines
the lower, upper, work, and merged directories, requires the work directory to
share a filesystem with the upper directory, and describes copy-up semantics.
See the
[`OverlayFS` documentation](https://docs.kernel.org/filesystems/overlayfs.html).

This is a strong same-host filesystem isolation technique when the runtime
explicitly provisions it. It is not provider-neutral here: Harbor exposes no
OverlayFS capability, setup requires a compatible Linux kernel and mount
permissions, and a task container commonly lacks the required privileges.
It still isolates only filesystem paths routed through the merged mount.

### Docker copy

Harbor's Docker implementation uses `docker compose cp`, falls back to engine
`docker cp`, and has tar fallbacks for uploads. See
[`docker_unix.py`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/docker/docker_unix.py#L142-L278).
Docker documents `docker cp` as a file/directory copy resembling `cp -a`; it
can target running or stopped containers, but cannot directly copy resources
under `/proc`, `/sys`, `/dev`, tmpfs, or user-created mounts. See
[`docker container cp`](https://docs.docker.com/reference/cli/docker/container/cp/).
This confirms that Harbor's Docker copy implementation is a transfer mechanism,
not a container snapshot.

## Docker-only `commit` plus fresh `create`/`start`

### What it can provide

A host-side implementation can locate the current Harbor main container,
commit it to a uniquely tagged temporary image, create a disposable container
from that image, start it, execute the completion check, and then remove the
container and image. Writes made by the check go to the disposable container's
writable layer rather than the scored container's layer.

Docker defines `commit` as creating a new image from a container's changes or
settings. It pauses the source container and its processes by default to reduce
the chance of corruption. See
[`docker container commit`](https://docs.docker.com/reference/cli/docker/container/commit/).
That pause means the operation is not completely non-interfering for a live
service, even though it need not change workspace bytes.

Docker defines `create` as preparing a new writable container layer over an
image without starting it, while `start` starts a stopped container. See
[`docker container create`](https://docs.docker.com/reference/cli/docker/container/create/)
and
[`docker container start`](https://docs.docker.com/reference/cli/docker/container/start/).
For checks whose required state is entirely in the main container's writable
layer, this is a higher-fidelity Docker-specific copy than transferring only a
workspace directory: it also captures changes such as installed packages and
files under `/etc`.

### Mount and volume limitation

Docker explicitly states that commits do not include data in mounted volumes.
Harbor's Docker environment uses runtime mounts for logs and may use task
mounts; task-specific Compose environments can add more. A committed image is
therefore incomplete whenever relevant state is mounted.

Reattaching an original read-write mount defeats isolation because the check can
modify the scored data. A correct Docker-specific implementation would need to
identify every relevant bind mount, named volume, anonymous volume, and tmpfs;
clone file-backed contents into disposable mounts; reproduce mount targets and
read-only flags; and accept that tmpfs and special kernel-backed mounts are not
ordinary snapshots. Docker documents that volume contents live outside a
container's lifecycle and that volume removal is a separate operation. See
[`Docker volumes`](https://docs.docker.com/engine/storage/volumes/).

### Process, service, and Compose limitation

An image records filesystem and image configuration, not the running process
tree, process memory, open descriptors, sockets, or current readiness state.
`docker start` starts the new container's configured PID 1; it does not resurrect
agent-started background services.

Harbor's generated Compose definitions run the main task container with
`sh -c "sleep infinity"` for both
[`prebuilt images`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/docker/docker-compose-prebuilt.yaml#L1-L4)
and
[`built images`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/docker/docker-compose-build.yaml#L1-L6).
A fresh committed container may therefore be alive and exec-ready while none
of the services started by the agent are running.

Committing only `main` also omits Compose sidecars and their filesystems. A
faithful reconstruction would have to recreate the Compose project, networks,
aliases, environment variables, ports, capabilities, resource constraints,
secrets, sidecars, health ordering, and cloned mounts. Harbor's own Docker
`start` performs stale-project cleanup and `compose up`, while `stop` chooses
between `compose stop`, normal `compose down`, or `down --rmi local --volumes
--remove-orphans`; see
[`DockerEnvironment.start`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/docker/docker.py#L953-L1019)
and
[`DockerEnvironment.stop`](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/environments/docker/docker.py#L1087-L1120).
Bypassing that lifecycle with raw `docker create/start` assumes responsibility
for all of it.

### Cleanup requirements

At minimum, a Docker-only implementation needs:

- collision-resistant names and ownership labels for every temporary image,
  container, network, and cloned volume;
- `try/finally` cleanup that removes the check container before its image;
- explicit cleanup of cloned named and anonymous volumes and temporary host
  directories;
- handling for timeouts, cancellation, daemon loss, partial `create`/`start`,
  and a killed harness process; and
- a later label-based garbage collector for resources leaked when in-process
  cleanup cannot run.

Each check can create a large image layer and incurs commit, create, startup,
and deletion cost. Host Docker access is itself provider-specific and highly
privileged. These costs and risks make the approach an optimization behind a
Docker-specific interface, not the baseline architecture.

## Service and process verification boundary

Filesystem isolation can safely answer questions such as:

- whether source code compiles when outputs are written into the clone;
- whether a test suite passes when its caches and generated files remain in the
  clone; and
- whether a package can be built from the copied workspace.

It cannot faithfully answer, without explicit reconstruction:

- whether an agent-started daemon is currently alive;
- whether a socket is listening in the scored environment;
- whether QEMU or another VM retains the required in-memory state;
- whether a sidecar contains expected runtime data;
- whether changes under `/etc`, system package directories, home directories,
  or database volumes exist when only the workspace was copied; or
- whether process-local environment, credentials, namespaces, firewall rules,
  or open files have the required state.

For these cases, the completion protocol should prefer read-only probes against
the live environment. If a probe itself may write, either provide a
deterministic startup recipe for a disposable environment and verify the
reconstructed service, or classify the property as not safely re-verifiable
under provider-neutral isolation.

## Recommended design for Evidence Harness

1. Add an orchestration-level isolated-check executor. Do not put provider
   discovery or raw Docker commands inside the agent loop.
2. Resolve the task's effective workdir dynamically. Preserve its absolute path
   in the disposable environment.
3. Start a fresh environment from the same resolved environment configuration
   and network/resource policy.
4. Stage selected state through Harbor's download/upload APIs. Make the copied
   roots explicit per check; do not silently imply that a workspace copy is a
   full environment snapshot.
5. Run checks in the disposable environment and collect only their receipts and
   logs.
6. Always call `stop(delete=True)` in cleanup. Record cleanup failure separately
   from check failure.
7. Classify checks that require live processes, sidecars, mounts, or files
   outside copied roots. Run only demonstrably observation-only variants on the
   scored environment.
8. Keep Docker commit support optional and capability-specific. Its result must
   never be described as provider-neutral or as preserving a live environment.

This design provides a defensible guarantee: potentially writing checks cannot
alter the scored workspace paths that were copied out. It deliberately does
not claim an impossible guarantee that a file copy reproduces all live runtime
state.

## Implemented Docker boundary

The first implementation uses the Docker-specific route because Harbor passes
an already-running environment to a custom `BaseAgent`, not an environment
factory. `DockerCompletionIsolation` checks the runtime shape, pauses the
single source container, records its diff, commits one candidate image, and
runs each check in an independent child with no network or mounts.

The implementation compares every Docker `C` path against an untouched child
from the candidate image. This extra comparison distinguishes a directory whose
metadata changed from a pre-existing file that a check replaced with a
directory. It removes each check child, the baseline child, and the image
after resuming the source. Normal operations reserve 95 seconds for cleanup,
and all cleanup operations share one 90-second hard deadline, including an
outer bound around the cleanup task. After any attempted pause, cleanup sends
`unpause` and verifies the source state. Any setup, deadline, inspection, or
cleanup failure prevents `verified`.

The production code remains narrower than the provider-neutral recommendation.
It rejects task Compose files, sidecars, task mounts, image-declared volumes,
devices, privileged settings, host namespaces, background processes, and
service checks. The 89-task census covers source-level configuration only.
Three source-bound replay experiments exercise the runtime path; see
[completion isolation experiments](completion-isolation-experiments.md).
All three replayed every recorded command with matching exit status and
received official reward 1.0. Replay output hashes may differ, so this does not
prove byte-identical candidate reconstruction. Mechanical isolation passed for
two. Neither of those two could complete semantic review because reviewer
credentials were unavailable. The third, `fix-ocaml-gc`, failed a frozen check
that required fixed summary text even though the official verifier passed. No
experiment ended as internally `verified`. This proves fail-closed execution
and cleanup, not score improvement; the canonical result remains 59/89.

## Primary sources

1. [Harbor 0.23.0 source at immutable commit](https://github.com/laude-institute/harbor/tree/1e5c5c6db929a10a140d05e606882c671ae20729)
2. [Terminal-Bench 2.0 source at the pinned commit](https://github.com/laude-institute/terminal-bench-2/tree/69671fbaac6d67a7ef0dfec016cc38a64ef7a77c)
3. [Docker: `container commit`](https://docs.docker.com/reference/cli/docker/container/commit/)
4. [Docker: `container create`](https://docs.docker.com/reference/cli/docker/container/create/)
5. [Docker: `container start`](https://docs.docker.com/reference/cli/docker/container/start/)
6. [Docker: `container cp`](https://docs.docker.com/reference/cli/docker/container/cp/)
7. [Docker: volumes](https://docs.docker.com/engine/storage/volumes/)
8. [Git: `git-worktree`](https://git-scm.com/docs/git-worktree)
9. [GNU Coreutils: `cp`](https://www.gnu.org/software/coreutils/manual/html_node/cp-invocation.html)
10. [Linux kernel: OverlayFS](https://docs.kernel.org/filesystems/overlayfs.html)
