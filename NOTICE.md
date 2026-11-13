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

发行版打包的 `bin/ffmpeg.exe` 与 `bin/ffprobe.exe` 来自
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) 的 GPL 构建。

* 版本：`N-128728-g49a3b1f259`（2026-11-10 构建）
* 许可：**GPL v3**（构建参数含 `--enable-gpl --enable-version3`，
  以及 `--enable-libx264 --enable-libx265`）

### 源码获取

对应源码可从以下位置获取：

* FFmpeg 官方源码：https://ffmpeg.org/download.html
  （对应提交 `49a3b1f259`，可用 `git checkout 49a3b1f259` 取得）
* 构建脚本与补丁：https://github.com/BtbN/FFmpeg-Builds

完整的 `--enable-*` 构建配置可通过运行 `ffmpeg -version` 查看。

### 分发说明

本项目的**源代码**采用 MIT 许可（见 `LICENSE`）。由于发行压缩包内**打包了**
GPL v3 的 FFmpeg 二进制，分发该压缩包时需同时提供上述源码获取方式。

本程序通过独立进程调用 `ffmpeg.exe`，未链接其库，因此本项目的源代码不受 GPL 约束。

## 测试夹具

`tests/` 中引用的 `testdata` 来自 unlock-music 仓库，单位为 64KiB 的加密片段
与对应的解密结果，仅用于验证算法正确性，不随本仓库分发（见 `.gitignore`）。
