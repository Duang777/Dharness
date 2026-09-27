# Completion isolation support census

This report classifies the frozen Terminal-Bench 2.0 matrix against the
task-source checks used by the Docker completion-isolation factory.

## Source bindings

- Dataset: `terminal-bench@2.0`
- Dataset source commit: `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`
- Matrix SHA-256: `51a6e58f5591d3d247543b53dc4f7008d663a085ca0928b2d4d2852f813f9939`
- Harbor version: `0.23.0`
- Factory source: `src/evidence_harness/docker_completion_isolation.py`
- Factory source SHA-256: `1b3712f48685728e53d1334fb4860762219480c45830993fc6497a7794469040`

## Result

- Tasks inspected: 89
- Statically supported: 89
- Statically unsupported: 0
- Runtime evaluated by this census: 0

This proves only that task source inputs do not trigger the Docker completion-isolation factory's task-config rejection rules.
It does not prove that the runtime container, process tree, image metadata, mounts, or isolated check execution satisfy the transaction's runtime checks.

## Task coverage

| # | Task | Static status | Source input SHA-256 | Rejection reasons |
|---:|---|---|---|---|
| 1 | `adaptive-rejection-sampler` | supported | `e981f4a0c46d5890ae1790d29ba6772e7a179b8d2246e6e609922b25ca599d1d` | None |
| 2 | `bn-fit-modify` | supported | `0eb3fa73239a04ae67356ff6b422a16625f5bf058ffe7fd4ee98d432c697a0b2` | None |
| 3 | `break-filter-js-from-html` | supported | `106e5231f940f3a40a6711d60e6c0344716d482f6ef68d31b480ee59c284ca52` | None |
| 4 | `build-cython-ext` | supported | `62b5ed67927679ccc454a2575fdbd784720caed852763401a672a87f646a8a19` | None |
| 5 | `build-pmars` | supported | `bb4ed7be4ff6d929028b2c30686d59013cca967a2c3f5e884aef3e9d7d4574aa` | None |
| 6 | `build-pov-ray` | supported | `0699854f6c927982da0e8ede579ef0010693a0f161b0b5cfeed7a7566cc100f6` | None |
| 7 | `caffe-cifar-10` | supported | `b2bfcc043fd19b77533fb9102c3755d3878e484a65da62d5ee1233957e992944` | None |
| 8 | `cancel-async-tasks` | supported | `f4d30f0c918f50eb9bdfcfc115d70f01cf7269c4f0dba134a3c3bcf655d16c04` | None |
| 9 | `chess-best-move` | supported | `1ae037df683e3eef4fdaf275b660933afdad5e08ae3ac6bc9fbe7a7a4246782c` | None |
| 10 | `circuit-fibsqrt` | supported | `3340c5b885f1ba01149213185dbb4bd07af289749ea98c5529b4ad962efefb50` | None |
| 11 | `cobol-modernization` | supported | `e97d44db12d0da3f2d638e823861c61531263f016e24d61472e13fbdde77cecd` | None |
| 12 | `code-from-image` | supported | `e2428165ba5a039542a1e3f864fdf1c66f69d85cb47f039ef808bfd82420b73e` | None |
| 13 | `compile-compcert` | supported | `9ec75806ea675dff1a8a88b0328065fe194827695bc462973eddec7294beab3e` | None |
| 14 | `configure-git-webserver` | supported | `9022ab1e4a8ccafff846a8490ce895d4218c8b20b0283ad71741178284de0afc` | None |
| 15 | `constraints-scheduling` | supported | `87eee0ccaea1700815f6504fbf5adf196eb4cb5c8a3632a50adadd67e787079c` | None |
| 16 | `count-dataset-tokens` | supported | `e9e1da039a591411c674ed26a12de664b948a08b4937ad05a85df334f7a5e6e2` | None |
| 17 | `crack-7z-hash` | supported | `126f36489c1eb7e92719cbb8594330748266ee3ceec1d283c5f319e4a4d9d406` | None |
| 18 | `custom-memory-heap-crash` | supported | `18b163667fab170e9970ca4749671b40b640a43491013b79b9a1dfecd60203c6` | None |
| 19 | `db-wal-recovery` | supported | `9656c8c708ff2203c5ecb18229f4973ac49ba1fbfb3f072059a43231dfee81e6` | None |
| 20 | `distribution-search` | supported | `085656a92c9cc08ec71f0175303e15c83377d7b023a16212518c96f2117a77f8` | None |
| 21 | `dna-assembly` | supported | `473204b31889dc0da333f671022291d5d2f5ac607796062dc839d5b92815cce5` | None |
| 22 | `dna-insert` | supported | `ed118f8894f4c4b76c6f4ff43c0d51bba45bf8d33aa12b11367b72dc7cb323b7` | None |
| 23 | `extract-elf` | supported | `9c93924a9f7b1e477fc7247d716340f7b9f8d21af7d1ee071fe0dc8e3745bfe5` | None |
| 24 | `extract-moves-from-video` | supported | `6ebff02cdb0045cef60c9c553dfe6c3c565d8abb56ddda227e8f8f38e7ad4dbf` | None |
| 25 | `feal-differential-cryptanalysis` | supported | `1bd9612ae4796197499c9d99317efd499a771f3b8531511b92ba34643b9ed92e` | None |
| 26 | `feal-linear-cryptanalysis` | supported | `583628a157c1b699a81a5ffecaf5e59b945f35418cbcfeada220c089f606e270` | None |
| 27 | `filter-js-from-html` | supported | `e47fca75df7d78193031d6b201fe7c6a158d96383df38725153f0342631a869b` | None |
| 28 | `financial-document-processor` | supported | `5e65c82b43a348651dd1bd9a384d39b12c529d4a831ae3a6565c06bcb4f86573` | None |
| 29 | `fix-code-vulnerability` | supported | `ae5c4a200577b26089a2fae37af3a66ec989f910872a06413863bd4459eb25bc` | None |
| 30 | `fix-git` | supported | `23bfa13383662f29381b6ba24eeec445632d755dc704620aac6223da1840f410` | None |
| 31 | `fix-ocaml-gc` | supported | `6c345648b916a63e90398b2b75d6d8c55cc8ded720f342c6dd36d7fc93ca95fb` | None |
| 32 | `gcode-to-text` | supported | `7a07a8f0eef557aab3519e217a2537cf2d39600febf8398d6b2d6b80ac7975f6` | None |
| 33 | `git-leak-recovery` | supported | `67b815322e6260bc9693e848a78950aaeb07f315fc4e4afb85bc5a589f397ab1` | None |
| 34 | `git-multibranch` | supported | `39b1bfb4c796cce618647db0a9cbe82de9037e677bdcaf98f032ee3c0c67688f` | None |
| 35 | `gpt2-codegolf` | supported | `0a2087a24c6e69c833b0d16d547848d7d839d1f1225a768b6544cebb0a278fd3` | None |
| 36 | `headless-terminal` | supported | `da0bcde4be4cb89b4c583e8f94efe5d462f08e5e30f2c346b9daec3b6992162d` | None |
| 37 | `hf-model-inference` | supported | `62000211794243b307b2eec25114118948fccb54865cfb87be499132189c72b2` | None |
| 38 | `install-windows-3.11` | supported | `e9e1191ff994064aa996dc73cb10668fd92debcd9ab2b0d2dbdfa9dfd15d5d0b` | None |
| 39 | `kv-store-grpc` | supported | `3fd3f28d9c263fc9d37f893760b31e2aba3670fa34f7e802344a89bcac7593be` | None |
| 40 | `large-scale-text-editing` | supported | `20271c3414e5b20a9ae30825f5b6ce078ae5a2eabd17b97318caaede71f30714` | None |
| 41 | `largest-eigenval` | supported | `c464eb3c990098f77bd2d41169c77424d75f15a315c5131e222a7f5476c284cf` | None |
| 42 | `llm-inference-batching-scheduler` | supported | `525b1d527b7f44ce625ab978d94d7900c43a7729f6dfa91c0860a90e1dfe6091` | None |
| 43 | `log-summary-date-ranges` | supported | `cfd4667837928ad314846144ee92cd45272f4bb7b203dc5e287b6fd6e179de0f` | None |
| 44 | `mailman` | supported | `fef2fe55247db98b2b44b0c89e1351c01b48223dc854337178a507055a2c1da1` | None |
| 45 | `make-doom-for-mips` | supported | `4fa4e84862f257533fa8c0a471071d2015b336e836beda5559ec7cc60cb7312d` | None |
| 46 | `make-mips-interpreter` | supported | `620a0e90970ba3bad57fd4635f3a4314cf3cbeb0785b5fad84da29fcfd9c271a` | None |
| 47 | `mcmc-sampling-stan` | supported | `e851fec0e06c91e7240aa765b7c7c9d51b30a29e3447c4cb0dadb889342443a9` | None |
| 48 | `merge-diff-arc-agi-task` | supported | `0265f14b4eccff0be0d5427cd3b940b3b1909f28ba253fe4ab25bc15fd263bbd` | None |
| 49 | `model-extraction-relu-logits` | supported | `d6c0645931cd8ec2c85e19ad336ae5e0e72690a225b1556feb2e8e82ed0156b8` | None |
| 50 | `modernize-scientific-stack` | supported | `efc52d1e2bc2dcac5253d14d2215e124e5d89d69db6048643efca583d6786940` | None |
| 51 | `mteb-leaderboard` | supported | `d30252ffb766b1ab31c1170d9c95de259105dc09a43b71042a7b1c797357aef8` | None |
| 52 | `mteb-retrieve` | supported | `710afd68fe67e478ed2841ef86b668a40257ed0cac104af71c75f4b56366e2b3` | None |
| 53 | `multi-source-data-merger` | supported | `bbccab2d382c604fb766d3a4a0e0a346b1beb32d104532c24acaba017776e649` | None |
| 54 | `nginx-request-logging` | supported | `c3139bc8ea35b21a5ca4643042b9fc74050ac40d682b8ea33230ed303520132f` | None |
| 55 | `openssl-selfsigned-cert` | supported | `9ce4616e62fda437f5eb8237968d50a743f0bc85afcfb226682f20601babff29` | None |
| 56 | `overfull-hbox` | supported | `d3c8dd7f2aa82e6e80d316ceb567c817f944e8f154d7113f111e290e56b9d32c` | None |
| 57 | `password-recovery` | supported | `688641293cc30ea8c3f7312c686ac903557ee6b159fdaee583eb2f3d97ff0f8d` | None |
| 58 | `path-tracing` | supported | `876ee09886bee9440605cdd746a26f9574e1ac9bc9755b46fcc261c16bf81324` | None |
| 59 | `path-tracing-reverse` | supported | `fd19c65fea23a9886a149aa4fb61675247c30c3ed01a9732176c6e8521c51c62` | None |
| 60 | `polyglot-c-py` | supported | `0953f91918c7a7ec78695410773afbe7e4ef90c5eb2802628455a6b5ece973ed` | None |
| 61 | `polyglot-rust-c` | supported | `7599d4206ff36ea83f78fff8d5386e6a9bc0838c27fe1e331fa3db1a6a1246d7` | None |
| 62 | `portfolio-optimization` | supported | `46856a0bdbe5a58d959fb358d21c5fd6ecf55213dde996b5aec9b349c65f662b` | None |
| 63 | `protein-assembly` | supported | `d1027388bd92e93b854b182ead48505ad58933c12d877eb3a7982da5bbf55384` | None |
| 64 | `prove-plus-comm` | supported | `b7f745d13ed1997ca189a04d96d8c412d01e1b2c1318714e077d906695218aeb` | None |
| 65 | `pypi-server` | supported | `5c0a32433cf1eed65bb9b390730cb19347d0bf2320e20113f0d53096bb1fbb1d` | None |
| 66 | `pytorch-model-cli` | supported | `b361d14ef8ba9970f29a8e0603115381ac4ab706194d692d0dcdd9f098f6084b` | None |
| 67 | `pytorch-model-recovery` | supported | `aefc6833918a4c30af699a1e6ca19fa77b6103d8578f65d63f17d431a453cffd` | None |
| 68 | `qemu-alpine-ssh` | supported | `71deaaa39b83f7a99346f04fe45fa472df31fccb115951cee6247956fb7d4067` | None |
| 69 | `qemu-startup` | supported | `d616830baba785a48b04d06453064ad044f338495004c0f0ac55828ece22b548` | None |
| 70 | `query-optimize` | supported | `f59f8b81bb43bfcdc30f89646371b31089ef846c0f753e943902bfb03ec2dcfc` | None |
| 71 | `raman-fitting` | supported | `6ea5e44062dd6ba92a5cc6b14e35af1975a7f06bb3b9dbc0f596f6f36e04fad9` | None |
| 72 | `regex-chess` | supported | `372152c7dd2f3bcd1ea7f04710d791e65064a183bc0946780e1b11eee0dbdddf` | None |
| 73 | `regex-log` | supported | `1e3c1afdc5dfd297368b7615a2459850dbe3f887d1e3f893a203fd9a707189ed` | None |
| 74 | `reshard-c4-data` | supported | `2986a2d3d5074baa3680587a75e5ab8cff8383a622956842e291007fb1012ce6` | None |
| 75 | `rstan-to-pystan` | supported | `21a895b60c18a8590f5097c2967360fe83dc1a76631ae241ddd484b536f30d07` | None |
| 76 | `sam-cell-seg` | supported | `e7d95650d8c6d5046a64974460a1138c64cbeea2748fcdd0596779972fc4866d` | None |
| 77 | `sanitize-git-repo` | supported | `82dbb4d027e55e9dc802817767123175d0e9e9fd546d264eab3ffa1496885aeb` | None |
| 78 | `schemelike-metacircular-eval` | supported | `ac80394c611e599bd128a47704d16c11096c0e16831533ae387f8f7c083bcd48` | None |
| 79 | `sparql-university` | supported | `64e98aada635d023598403f518f540c52043e10bd437742f14617586003f2983` | None |
| 80 | `sqlite-db-truncate` | supported | `819c879da64b21ab9dbb3caeb12606930e2adc08c3818ea0bdcb8e4739e4dec1` | None |
| 81 | `sqlite-with-gcov` | supported | `3f916d9e82f792c78a1d90e4debf51ed69c4679acae9ad47dbb63d1007139836` | None |
| 82 | `torch-pipeline-parallelism` | supported | `08af0e0e9b561753ed8d66d4d32b09c2d22b4595e19c741810fbd056643b3b5a` | None |
| 83 | `torch-tensor-parallelism` | supported | `61be05937b9a1c3d7a7ea5662bbecaf1c6569f1a0afd977eef4b670aea288d91` | None |
| 84 | `train-fasttext` | supported | `513a7e44b85fe477028741e92a6300049a5bab81e4275729cd0ccb304dca6a18` | None |
| 85 | `tune-mjcf` | supported | `ac1dec6970801a903ba87a0ea46b4c276483bb02af5d472712b50e7f437ab411` | None |
| 86 | `video-processing` | supported | `bae2edcf6cd11cc275371cff7d77482de9180c089961fb2dd86eb0c431b9d7e6` | None |
| 87 | `vulnerable-secret` | supported | `57495fcb749bd97c6cb39f4de04990045d33185f35cf6943d34e604507a6e719` | None |
| 88 | `winning-avg-corewars` | supported | `8f00a163c5836398de7bf5a1aba2c05819756499ae4d159517f2c2a8a43a9e69` | None |
| 89 | `write-compressor` | supported | `76dbe66088caf2f1d36723fc564ab5405d7862294a9fbf3a31bbe342e7d5e4ca` | None |

## Runtime boundary

The census cannot prove these transaction-time requirements:

- The Docker daemon reports a Linux OSType.
- The Harbor Compose project has exactly one running main container and no sidecars.
- The source is running, unpaused, and has only Harbor's standard keepalive process.
- The effective image declares no Docker volumes.
- The effective HostConfig uses only the supported flags and namespaces.
- Runtime mounts target Harbor control paths only and do not expose docker.sock.
- The source diff remains unchanged while the source stays paused for the snapshot.
- A mount-free, network-disabled child starts and contains the shell and check dependencies.

A task marked `supported` is eligible for a runtime isolation attempt.
It is not evidence that the attempt will succeed.
