# v1.0.8 待办与进度（2026-10-24）

## 一、本轮三件事（用户 14:31 / 14:34 提）

1. **表格列宽拖动有问题** —— 已修
2. **本机地址应显示 localhost** —— 已修
3. **模型管理要按「供应商 / 模型」两个维度（对齐 Cherry Studio 模型服务）** —— 已改

## 二、改了什么

### 1. 列宽（`ui/table.py`）

- 根因：`Column(stretch=True)` 交给 Tk 自动布局。内容比可视宽时，**任何一次
  `<Configure>` 都会把可拉伸列压回 minwidth**。实测：拖 +60 像素，过程里
  255→309 正常跟手，**一松手塌成 72**。
- 改法：
  - 所有列 `stretch=False`；
  - `_filler_key()` 选填充列（第一个 `Column.stretch=True`，否则最后一列）；
  - `_refit_columns()`：`want = 用户宽度`，总和不足可视宽时把差额补给填充列，
    超出则一个字不动（交给横向滚动条）；**只增不减**；
  - `<ButtonRelease-1>` → `_remember_widths()` 把当前宽度记成用户设定值；
  - `<Configure>` debounce 40ms 再 refit；
  - `_xscroll_set` 不再同步 `grid()`，改成记状态 + 80ms debounce 应用。
- 验收：`_tableresize.py` **12/12**。

### 2. localhost（`core/hosts.py`）

`addr_of()` 开头加：`if proto_of(p) == "local": return "localhost"`。
影响面：主机清单地址列、右键「复制地址」。`_restest` 补 2 条（**215**）。

### 3. 模型管理两级（`pages/model_page.py` + `core/aiops.py`）

- 左栏 `_ProviderList`（自己画，两行文字、跟随主题、当前供应商有 ●），
  顺序 `PROVIDER_ORDER = deepseek / ollama / lmstudio / dsh / custom`；
- 右侧上方供应商卡：接口地址、API Key、保存设置 / 试连 / 清除 Key；
  下方是该供应商下的模型表（去掉了「服务商」列）；
- `ai.json` 新增 `providers: {pid: {base, key_enc}}`；
- 新增 `AIConfig`：`provider_meta / provider_base / provider_key_enc / provider_key /
  provider_models / set_provider`；
- `set_provider` 下发规则：模型 base 为空**或等于旧供应商地址**才跟着改，
  用户单独改过的地址不动；当前模型的扁平镜像同步更新；
- `upsert_model` 在 base/key 为空时继承供应商那一份；
- 老配置迁移：`providers` 缺失就从该供应商第一条模型推初值，不弹窗。
- 验收：`_modeltest` **110/110**（新增 16 项供应商维度）、`_aimodeltest` **42/42**
  （改成按供应商断言）。

## 三、顺序依赖（发版别搞反）

```
版本号 1.0.8（app.py APP_VER + _pack_release.VER/STAMP）→ 全量回归 → 打 exe
→ _texe 验收 → _pack_release 打四个包 → 提交 → tag v1.0.8 → 推四端
→ _pack_release --only github → _deploy_share.py 1.0.8 → 覆盖 D:\Tools → 重建快捷方式
```

- `--only` 现在两种写法都认（空格/等号），名字写错返回 2。
- 共享盘 SMB 不许 unlink/覆盖，`_deploy_share.py` 自己会顺延 `.r2/.r3`。

## 四、已知坑（本轮新增）

- **Tk Treeview 的 stretch 不能用来做「铺满」**：内容超宽时它会把列压到 minwidth，
  而且触发点是任意 `<Configure>`，不是只有窗口 resize。要铺满自己算。
- `xscrollcommand` 回调里**不要同步改布局**（grid/grid_remove）：拖动列宽时
  每像素都调一次，手感直接变卡，还会连锁触发 `<Configure>`。
- `ModelDialog` 的列宽总和要压在 **~850 逻辑像素**以内（1100 窗宽 − 边距 − 196 左栏），
  否则最右的「状态」又会被挤出去。
- `pack()` 的控件用 `grid_remove()` 收不起来（反之亦然）。「清除 Key」这轮
  就因为混用而一直显示，改成 `pack_forget()` / `pack()`。
- 项目 venv：`C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe`
  （`C:\Python314` 缺 cryptography，`C:\Program Files\python` 是 3.8.6 太老）。

## 五、当前状态

- [x] 三件事的代码与自测
- [x] 全量回归 22 套全绿
- [x] 版本号 1.0.8
- [x] 两个 exe 重打（14:58）
- [ ] `_texe` 验收
- [ ] 四个包 + 提交 + tag + 推四端
- [ ] 共享目录 + 本机 D:\Tools + 快捷方式
