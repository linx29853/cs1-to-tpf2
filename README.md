# CS1 → TpF2 建筑转换器 1.0beta ⚠仍在开发⚠

从 [Releases](https://github.com/linx29853/cs1-to-tpf2/releases/latest) 下载免安装包，完整解压后双击 `app/CS1ToTpF2/CS1ToTpF2.exe`。程序免安装，不需要 Python、Blender、ModTools 或 CS1 游戏。
请完整解压，保留 EXE 旁边的 `_internal` 文件夹，不要只复制 EXE。

## 转换

1. 点击“添加 CRP 文件”，或“添加文件夹”递归扫描资产目录。
2. 选择保存目录。可选填原资产作者；批量转换时所有文件使用同一署名。
3. 点击“开始转换”。任务在后台运行，日志和进度会更新；可取消，未完成的临时输出会清理。
4. 选择已完成的文件，查看贴图预览、尺寸、三角面数和转换提示。
5. 输出包含完整模组文件夹及 ZIP 安装包；勾选“保留可编辑 OBJ”还会生成主模型和 LOD 的 OBJ。

同一文件的模组标识由原包内容生成，因此重复转换会提示输出已存在。软件不会覆盖或删除已有结果。
需要重新转换时，选择一个新的保存目录即可。

## 安装

选择已完成的行，点击“安装选中结果到游戏…”，选择包含 `TransportFever2.exe` 的游戏根目录。
程序只复制到 `mods` 子目录，逐文件校验，并拒绝覆盖同名模组。也可以手动解压 ZIP 到 `mods`。

正确结构：`mods/cs1_xxxxxxxxxxxx_x_1/mod.lua`。不要多套一层文件夹，不要修改名称末尾的 `_1`。

进入游戏，在模组列表启用对应的“建筑名（自用摆件）”，然后到造景菜单查找建筑。
支持放置前高度调整；包含加法发光材质的建筑会提供发光装饰开关。
游戏目录中已有旧的金山广场自用模组时，无需再安装软件生成的同一建筑副本。

## 首版范围

- 只处理 CS1 的建筑 CRP。支持已验证的包结构、主建筑、包内子网格与子建筑、DDS/PNG 贴图、多材质、原有 LOD。
- 保持原始比例、三角面和 UV；统一为 TpF2 坐标。**没有自动减面功能**。
- 没有独立 LOD 的部件在远景沿用主网格，避免突然缺件。
- 建筑之外单独布置的树木、路灯、车辆等道具不迁移；数量会列在预览提示和 `report.json` 中。
- 不迁移住户/就业/商业/生产逻辑，也不迁移动画、原游戏夜间窗灯、XYS/ACI 法线或颜色变化。
- 使用与首次金山广场转换一致的基础材质策略：不透明漫反射、玻璃高光近似、静态 EMISSIVE 发光。
- 支持标准建筑、Legacy Diffuse 和加法发光材质；特殊着色器、缺失建筑依赖、条件部件会明确报错。
- 输出经文件级验证，但软件不会自动启动游戏、修改游戏设置或加载存档。新资产的最终显示仍应在 TpF2 中确认。
- 全程本地离线处理；不下载、不上传、不发布原始资产。

## 验证

已用用户提供的 8 个真实 CRP 完成批量转换，并用打包后的 EXE 再次测试。
首栋金山广场已由用户确认游戏内外观和比例正确；软件重建版的几何、UV、法线已与该版本读回核对。
其他 7 个资产通过离线转换和配置验证，未逐个在游戏内实测。
另有损坏文件、UTF-8 长名称、变换/法线、循环引用、缺失依赖、多层材质、不覆盖和取消清理测试。
记录见 `验证记录.json`。

## 源码

`source/` 包含可编辑 Python 源码及测试。
开发环境使用 Python 3.12（含 Tk），运行 `pip install -r requirements.txt` 后执行 `python main.py`。
运行测试：`python -m unittest discover -s tests -v`。
Windows 打包：运行 `build.ps1`；使用 PyInstaller 生成免安装目录。

命令行批量转换（源码运行）：

```powershell
python main.py --cli "D:\CS1Assets" --output "D:\Converted" --report "D:\Converted\batch.json"
```

可选 `--no-preview`、`--no-obj`、`--author "作者名"`、`--lod-distance 350`。
窗口版 EXE 也支持 `--cli`，但无控制台输出，建议同时使用 `--report`。

## 格式参考与第三方组件

- [tony56a/crp-parser](https://github.com/tony56a/crp-parser)：CS1 资源序列化格式参考，MIT 许可随源码附带。
- [LiamBrandt/crp-extract](https://github.com/LiamBrandt/crp-extract)：早期手动解包流程参考；软件不依赖或运行它。
- [TpF2 官方网格格式](https://wiki.transportfever2.com/doku.php?id=modding:resourcetypes:msh)、[材质格式](https://wiki.transportfever2.com/doku.php?id=modding:resourcetypes:mtl)。
- Python、NumPy、Pillow、Tcl/Tk 等组件的许可见 `licenses/` 及运行库附带许可。

原始建筑模型和贴图的作者权利不因格式转换而改变。软件与验证记录不附带用户的 CRP 资产。
