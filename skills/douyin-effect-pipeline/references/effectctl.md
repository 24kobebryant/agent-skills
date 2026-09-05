# 只读 CLI 与版本化证据

入口：`python3 <skill-dir>/scripts/effectctl.py <command> <project> [--json]`。
无需安装依赖。默认不写工程、不清缓存、不上传、不操作 UI。

| 命令 | 能证明什么 | 不能证明什么 |
|---|---|---|
| doctor | 结构检查、进程候选、重复路径线索 | 未暴露工程路径的进程属于哪个工程 |
| assets | 原有资源检查、序列源帧数 | 原生运行时已导入同样帧数、动画自然 |
| size | 磁盘总量、目录/大文件排行、指定效果包字节数 | 原生源工程打包后大小 |
| snapshot | 当前源指纹、指定包指纹 | 包由当前源导出 |
| evidence | 记录绑定的源/包/证据文件是否仍一致 | 截图语义正确、真机实际执行 |
| preflight | 前五层及发布资料记录完整性 | 审核通过、最终提交授权 |

退出码：0=本命令未发现阻断，1=发现错误/超限/必需证据未齐，2=输入或执行失败。
`assets` 返回 0 仍可能有 warning 和 runtime not_run；不得映射成真机通过。
`preflight` 最好的状态仅为 `record_integrity_ready`，之后仍检查其 manual_checks。

```bash
python3 <skill-dir>/scripts/effectctl.py doctor /absolute/project --json
python3 <skill-dir>/scripts/effectctl.py size /absolute/project --package /absolute/export/effect.zip --project-limit-bytes 200000000 --json
python3 <skill-dir>/scripts/effectctl.py snapshot /absolute/project --package /absolute/export/effect.zip --json
python3 <skill-dir>/scripts/effectctl.py preflight /absolute/project --package /absolute/export/effect.zip --json
```

示例 200000000 是检查参数，不是永久平台规则。根据当前 UI 确认限额和单位；源工程限额与效果包限额分开核对。CLI 不推测平台压缩排除规则。

## 证据记录协议 v1

跨会话项目保留原 `.douyin-effect/state.json` 用于决策；另外在取得项目写入授权后维护 `.douyin-effect/evidence.json`。旧状态不自动迁移为新证据。

```json
{
  "schema_version": 1,
  "records": []
}
```

每次验证后通过正常文件编辑追加记录，不覆盖历史：

- `layer`：source / compile / editor_preview / phone_preview / effect_detection / release_materials / submission / review。
- `status`：passed / failed / not_run / blocked。
- `source_sha256`：验证时 snapshot 输出；不能在源码变更后给旧证据补盖当前指纹。
- `package_sha256`：手机、检测、提交、审核必填。真实导出时确认源版本，并记录包对应关系；CLI 不能自行证明此关系。
- `observed_at`：实际观察时间（ISO 8601），不是补写记录时间。
- `environment`：实际编辑器版本、运行端/设备，以及必要的测试输入。
- `artifacts`：非空数组，每项为绝对 `path` 与文件 `sha256`。只存去敏证据，路径可在工程外。
- `effect_id`：submission / review 必填，关联同一特效；审核失败追加 review failed，不抹掉原提交回执。
- `notes`：复现操作、观测结果、尚未验证边界；发布资料记录应包含最终名称、提示、摄像头和图标上传完成证据。

源指纹覆盖工程内全部普通文件（含 Icon 和未知目录），只排除顶层或嵌套 Library、.git、.douyin-effect 目录。符号链接不跟随；不支持用指纹为外部链接资源背书。缓存不属于源证据，运行资源仍须单独验证。

证据、视频、备份、素材源稿默认放工程外；通用脚本归 Skill。不要为节省包体删业务引用资源；`size` 不自动删除任何内容。

## 回归

`python3 <skill-dir>/scripts/test_effectctl.py` 使用临时假工程，不打开像塑、不联网、不提交。它验证工具逻辑，不等于真实 Agent / 真机 E2E。
