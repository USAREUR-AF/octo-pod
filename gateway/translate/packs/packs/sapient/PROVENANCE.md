# Provenance of the schema sources

Everything under `sources/` is an unmodified copy. `tools/gen/proto2pack.py`
reads these files and nothing else.

## SAPIENT (BSI Flex 335 v2.0)

| | |
|---|---|
| Repository | https://github.com/dstl/SAPIENT-Proto-Files |
| Commit | `244a741c13bcee628f84944aef91e4a66854bd7d` (committed 2024-06-04T14:35:47Z; the head of the default branch when fetched, 2026-11-15) |
| Files taken | `bsi_flex_335_v2_0/*.proto` (16 files), `proto_options.proto`, `LICENCE.txt` |
| Fetched from | `https://raw.githubusercontent.com/dstl/SAPIENT-Proto-Files/<commit>/<path>` |
| Not taken | `bsi_flex_335_v1_0/`, `Readme.md`, `20230710-SAPIENT_v7-BSI_Flex_335_v1_Differences.pdf` |

`LICENCE.txt` at that commit, in full:

```
Crown owned Copyright 2023

Except where noted otherwise, the SAPIENT Protobuf files are
licensed under the Apache License, Version 2.0 (the "License"); 
you may not use this file except in compliance with the License. 
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

Each of the 17 `.proto` files begins with the line
`// Dstl (c) Crown Copyright 2024` and carries no other license or copyright
text, so no file notes an exception to the license above.

The repository's `Readme.md` at the same commit says of the standard document
(not of the protobuf files): "The British Standards Institution retains
ownership and copyright of this BSI Flex."

The imports of `google/protobuf/timestamp.proto` are not satisfied from a file:
the generator carries the two fields of `google.protobuf.Timestamp`
(`int64 seconds = 1`, `int32 nanos = 2`) in its own source.
`google/protobuf/descriptor.proto` is imported only for `extend` blocks, which
the generator skips.

SHA-256:

```
d7c96112b11c69ef0332e91dbe79115672535b5f117c75432f8b6604f8c594f6  sapient/LICENCE.txt
9e9870d6b8f497969ee6283d4664c47a68ce036c83b3355422c312fc721f2268  sapient/proto_options.proto
2feaec808296dc90a1f64d333bca6bc750edb48da1f198f65d9ac5711c5307c1  sapient/bsi_flex_335_v2_0/alert_ack.proto
d9c4392c9e63f68bba776755057527786f889b4022c97252cf14a26eacd7d3ff  sapient/bsi_flex_335_v2_0/alert.proto
35615503d11d5e8a42073531ff8bfd8b8c4f2eb92af125f53bc9a8106fdf3244  sapient/bsi_flex_335_v2_0/associated_detection.proto
128bb94aed8c8b47594c89d672f8c26cf8e658567428e0b15e26e1892eeebbf8  sapient/bsi_flex_335_v2_0/associated_file.proto
d597b0bac12080f004ec43c481c817a3ecf0df820cc4908451eb5a34573b04b6  sapient/bsi_flex_335_v2_0/detection_report.proto
977dbe0d6434712d5ede2c5c65d7c3b80b008f94a8330d2bc1ede56b8cc2db23  sapient/bsi_flex_335_v2_0/error.proto
cdcf2ec4265ad63349c2ee96e704f7113ff398f958520f3b1c75153b39873dbc  sapient/bsi_flex_335_v2_0/follow.proto
3a7d3c000349176cf5fdbe5b5ab0dd8e1cef061815b32c43621478e39daadcca  sapient/bsi_flex_335_v2_0/location.proto
9230fe7943f2b8b28daee40ec2f4357467f256b26496d44bfc3cab8e8f646ab3  sapient/bsi_flex_335_v2_0/range_bearing.proto
fbb4b4e7f09e7bf68369e4b9664cb5dd59245991310f2637608692a17fcf1a35  sapient/bsi_flex_335_v2_0/registration_ack.proto
84ad97823aa1ed8f3dbaf18adfea3d4a01e68b26e62619baa6bea5f52c699c5a  sapient/bsi_flex_335_v2_0/registration.proto
e11ea1d3fe3fbccb13aafb53bca2187fc2e2e66418578a91cfdb42f89a055426  sapient/bsi_flex_335_v2_0/sapient_message.proto
a2675a6d0c0026912e2cb750fd93de0bdaa2c59d32cd424cacb694fc7c83db23  sapient/bsi_flex_335_v2_0/status_report.proto
78b94c1c75e227f50b5bf4afde729b8bc2666cac3f675b13cb9b6084d2c3e3b7  sapient/bsi_flex_335_v2_0/task_ack.proto
956076d43ac06f79df711fa0c5a695f1c10102389eb4c25726936f30df8161a2  sapient/bsi_flex_335_v2_0/task.proto
ac74090ecf1d7bcb68376ca5780e7b096fbdd524c8055837ace26502f4f14dad  sapient/bsi_flex_335_v2_0/velocity.proto
```

## ISA (Integrated Sensor Architecture, IPL 3.7)

| | |
|---|---|
| File | `isa/ipl-3.7-6.0.13.proto` (3496 lines, proto2, package `DataModel`) |
| Obtained | from the publisher, in the ISA 6.0.13 schema set (file `ipl-3.7-6.0.13.proto`); there is no public download address |
| Identification in the file | `ISA Protocol Buffers Declaration File`, `ISA IPL Format 6.0 (Iteration 149)`, `Model Version: 0x01`, `Git Hash: c02e800` |

Distribution statement, lines 1 to 3 of the file, verbatim:

```
//! 
//!   Distribution Statement A. Approved for Public Release. Distribution is Unlimited.
//!   
```

The file carries no license text and no copyright line.

SHA-256:

```
a959dde49e752d326b8e050a07d798d131e59853e45c74add51b02b339235927  isa/ipl-3.7-6.0.13.proto
```
