# 第三方代码与二进制声明

本项目包含移植的算法与打包的第三方二进制，版权归原作者所有。

## QMC / QMCv2 解密算法

`platforms/qmc_algo.py` 移植自 **unlock-music**：

* 源码：`src/decrypt/qmc_cipher.ts`、`src/decrypt/qmc_key.ts`、`src/decrypt/qmc.ts`
* 许可：MIT License
* 版权：Copyright (c) 2019-2023 MengYX
* 上游：https://git.unlock-music.dev/um/web

## 网易云 ncm 解密

`platforms/netease.py` 移植自 **unlock-music**：

* 源码：`src/decrypt/ncm.ts`
* 许可：MIT License
* 版权：Copyright (c) 2019-2023 MengYX
* 上游：https://git.unlock-music.dev/um/web

## TEA 实现

`qmc_algo.py` 中的 `TeaCipher` 移植自 `src/utils/tea.ts`，其源头为 Go 标准库扩展：

* 许可：BSD 3-Clause
* 版权：Copyright 2015 The Go Authors
* 上游：https://go.googlesource.com/crypto/+/master/tea

## FFmpeg

发行版打包的 `bin/ffmpeg.exe` 与 `bin/ffprobe.exe` 来自：
https://github.com/BtbN/FFmpeg-Builds （GPL 构建）

FFmpeg 采用 LGPL / GPL 双许可，本项目的构建使用了 GPL 版本。
因此发布的压缩包整体分发时需遵守 GPL v3 条款，并附上 FFmpeg 源码获取方式。

## 测试夹具

`tests/` 中引用的 `testdata` 来自 unlock-music 仓库，单位为 64KiB 的加密片段
与对应的解密结果，仅用于验证算法正确性，不随本仓库分发（见 `.gitignore`）。
