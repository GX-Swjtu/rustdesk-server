# GX 服务端构建与发布

本仓库保持独立。RustDesk 客户端仓库的联合发布使用固定的本仓库提交直接构建，不要求这里先有 tag 或 Release，也不会替本仓库创建 Release。

- 只构建：在 Actions 运行 **Fork server build (artifacts only)**，选择需要的分支或提交。产物保存在该次运行的 Artifacts，不创建 Release。
- 独立发布：手动创建新的 `server-v1.1.17-gx.1` 格式 tag，基础版本必须等于 Cargo.toml。推送后 **Fork server release (draft only)** 构建并生成草稿；检查后人工发布。每次更新使用新的 gx 序号，不移动旧 tag。
- 不单独构建或发布：不要运行上述工作流或推送上述 tag，联合发布仍可正常编译本仓库。

GitHub 的手动运行按钮要求工作流存在于仓库默认分支。若默认分支尚未合入这些文件，可在本仓库执行 `git push origin HEAD:refs/heads/build/server/check-001`，由专用构建分支启动同一套只产出 Artifacts 的流程；不会创建 Release。后续检查换一个分支名，或更新已有构建分支。

新流程不使用 Docker Hub/GHCR，不需要镜像仓库凭据。上游的 `build.yaml` 保留原状；使用新的 `server-v...-gx...` 标签避免触发上游 Docker 发布流程。

当前 fork 服务端的 Native P2P 依赖支持 Linux、Windows，统一入口构建 Linux x86_64、Linux ARM64 和 Windows x86_64；不声明 macOS 服务端支持。客户端的发布矩阵独立，账号服务也有自己的平台矩阵。

每个 ZIP/TAR 包包含 `hbbs`、`hbbr`、`rustdesk-utils`、许可证、部署参考和 `SOURCE.json`，记录服务端及其 hbb_common 的完整提交。Linux 包在 Ubuntu 24.04 构建，请使用 Ubuntu 24.04 或具备兼容运行库的主机；Windows 包未作 Authenticode 签名。不会打包工作目录的密钥、数据库或 `.env`。

完整的新部署、升级备份与客户端填写步骤见 [联合部署指南](https://github.com/GX-Swjtu/rustdesk/blob/codex/native-p2p/docs/server-deployment.md) 和 [客户端配置指南](https://github.com/GX-Swjtu/rustdesk/blob/codex/native-p2p/docs/client-setup.md)。联合 Release 同时附带对应版本的离线指南。

本仓库只提供 ID/信令和中继；账号 API 来自独立的 rustdesk-account-server。需要账号登录时，hbbs 显式设置 `HBBS_ACCOUNT_HANDSHAKE=Y`，客户端填写同一套 hbbs 的公钥。不要把服务器私钥、账号主密钥或登录令牌写入客户端 Key。
