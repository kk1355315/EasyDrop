# iPad 快捷指令

仓库中的 [接收文件新.shortcut](接收文件新.shortcut) 是从 iPad 导出的 Apple 签名快捷指令，可以直接导入。

SHA-256：

```text
C2A1D1BDF282A483228A6C0652816E419F96D334AFC92962B6F30B3DF6287147
```

## 导入

打开 `.shortcut` 文件并添加快捷指令。安装时会询问两个配置：

1. DropPull 服务器地址，例如 `https://drop.example.com`。
2. DropPull 访问 Token，即服务器 `.env` 中的 `PULL_TOKEN`。

服务器地址只填写协议和域名，不要在末尾添加 `/drop`。快捷指令会自动请求：

```text
[服务器地址]/drop/api/pending
```

图片和视频保存到“照片”。其他文件保存到：

```text
iCloud Drive/easydrop/
```

## 邮件自动化

个人自动化无法随快捷指令文件一起分发，需要在设备上创建一次：

```text
快捷指令 → 自动化 → + → 电子邮件

主题包含：DROP_READY
账户：接收触发邮件的账户
运行方式：立即运行
动作：运行快捷指令 → 接收文件新
```

第一次请在解锁状态手动运行快捷指令，允许网络、“照片”和 iCloud Drive 权限。

## 发布文件检查

该文件只包含通用服务器占位地址和 Token 导入问题，不包含维护者的真实域名、访问 Token 或设备专属文件夹引用。
