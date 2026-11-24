# 智能驾驶场景查询系统

基于 nuScenes 的单相机离线场景回查。支持按录制时段、预设路口和急刹估算事件查询。配置 Milvus 后，可按中文画面描述检索，或与时空条件一起使用。详情页可查看自车轨迹、急刹估算位置及最多 5 张前向相机关键帧。程序使用既有图片文件，数据库保存其相对路径。

## 数据准备

默认读取项目目录下已解压的 `v1`。服务器也可把数据放到独立目录，通过 `--dataset` 指定。数据目录至少包含下列内容。

```text
v1/
├── v1.0-mini/          # scene.json、sample.json、sample_data.json 等元数据
└── samples/CAM_FRONT/  # 前向相机 JPEG 图片
```

- 导入器按 `scene` 和 `sample.scene_token` 组织场景，从 `sample_data` 选取 `CAM_FRONT` 的标注关键帧。`sensor` 与 `calibrated_sensor` 的关联用于识别通道。图片按拍摄时间分布选取，保留首尾帧。
- 自车轨迹取 `sample_data.ego_pose_token` 对应的 `ego_pose.translation` 前两维。坐标是地图平面米制坐标，空间查询同时匹配 `log.location`，以免不同地图的坐标混用。时间存为 Unix 微秒，界面按 UTC+08:00 显示，时段采用 `[开始, 结束)`。
- 本地 mini 包有 10 个场景。该包的元数据未提供急刹真值标签，导入器从相邻前向相机位姿计算平面速度差。上一个区间速度达到 2 m/s 且估算加速度 ≤ −2.5 m/s² 时，记录为急刹候选，相邻候选保留减速最明显的位置。
- `regions.json` 的三个矩形为参考前向画面与对应轨迹预设的演示路口范围，`reference_scene` 记录对应场景。路口边界计为命中。只选路口时匹配时段内轨迹点，勾选急刹时匹配同一个事件的时刻和位置。

数据字段与坐标说明见 [nuScenes 官方教程](https://www.nuscenes.org/tutorials/nuscenes_tutorial.html) 和 [nuScenes 数据结构](https://github.com/nutonomy/nuscenes-devkit/blob/master/docs/schema_nuscenes.md)。地图矢量数据属于另行发布的 [Map Expansion](https://www.nuscenes.org/tutorials/map_expansion_tutorial.html)。

## 在服务器上使用 PostgreSQL/PostGIS

以 Linux 和 Python 3.10+ 为例，从本项目目录执行。项目数据库用于保存一套数据。每次导入会在同一个事务中刷新项目表，重新使用原有场景、轨迹和关键帧编号。

使用服务器已有 PostgreSQL 时，请由数据库管理员创建独立数据库和登录用户，并启用 PostGIS。例如以管理员身份进入 `psql` 后执行：

```sql
CREATE ROLE scenes LOGIN;
\password scenes
CREATE DATABASE scenes OWNER scenes;
\connect scenes
CREATE EXTENSION IF NOT EXISTS postgis;
```

服务器已配置 Docker Compose 时，可用项目内的 `compose.yaml` 启动独立数据库。示例只绑定服务器回环地址 `127.0.0.1:55432`，使用配置文件里的演示账号。

```bash
docker compose up -d --wait
```

安装 Python 依赖、指定路径和连接、导入并启动。下面的连接串对应 Compose 示例。使用已有实例时，替换端口、数据库名和用户名。在终端提示时输入数据库密码。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt 'psycopg[binary]>=3.2,<4'

python import_data.py --dataset /srv/nuscenes/v1 \
  --database-url 'host=127.0.0.1 port=55432 dbname=scenes user=scenes' --ask-db-password
python app.py --host 127.0.0.1 --port 8000 --dataset /srv/nuscenes/v1 \
  --database-url 'host=127.0.0.1 port=55432 dbname=scenes user=scenes' --ask-db-password
```

在自己的电脑上开一条 SSH 转发，即可用浏览器访问服务器上的查询页面。

```bash
ssh -L 8000:127.0.0.1:8000 用户名@服务器地址
```

访问 <http://127.0.0.1:8000>。数据库区域判定使用 [PostGIS ST_Covers](https://www.postgis.net/docs/manual-3.3/ST_Covers.html)。容器和数据库驱动见 [PostGIS 镜像文档](https://github.com/postgis/docker-postgis) 和 [Psycopg 安装文档](https://www.psycopg.org/psycopg3/docs/basic/install.html)。

## 用现有 Python 本地预览

当 `DATABASE_URL` 为空时，程序用 Python 自带的 SQLite。只需 Python 和 Flask，数据库文件存放在 `.runtime/scenes.sqlite3`。在项目目录执行：

```powershell
python import_data.py
python app.py
```

导入完成后，mini 数据应包含 10 个场景、404 个轨迹点、1 个估算急刹事件、50 张关键帧和 3 个预设路口。

## 查询示例

| 条件 | 预期结果 |
| --- | --- |
| 仅勾选急刹估算 | `scene-0103`，事件时间 `2018-08-02 03:26:49.512404`（UTC+08:00） |
| 波士顿 · 转弯车辆路口，勾选急刹估算 | `scene-0103`，位置约 `(640.42, 1617.63)` m，加速度约 −2.89 m/s² |
| 波士顿 · 信号灯路口 | `scene-0757` |
| 波士顿 · 信号灯路口，勾选急刹估算 | 0 个场景 |
| UTC+08:00 的 `2018-08-02 03:26:48` 至 `03:26:49`，勾选急刹估算 | 0 个场景 |
| UTC+08:00 的 `2018-08-02 03:26:49` 至 `03:26:50`，勾选急刹估算 | `scene-0103` |

`GET /api/regions` 返回路口清单。`GET /api/scenes?region=boston-turn&brake=1` 返回场景、代表帧及筛选证据，可加 `start` 和 `end` 两个 ISO 时间参数。对含 `+08:00` 的时间使用 URL 编码。`GET /api/scenes/<id>` 返回轨迹、事件与关键帧地址。

## 导入画面语义并启用向量查询

在 Milvus 已启动的 Ubuntu 服务器上，进入项目目录并新建 Python 3.10 或更高版本的虚拟环境。下面使用 CPU 版 PyTorch，并用虚拟环境内的 Python 执行命令。

```bash
cd /data/hdd1/zh_data/my/code
python3 -m venv .venv-qwen
./.venv-qwen/bin/python -m pip install torch==2.10.0 torchvision==0.25.0 \
  --index-url https://download.pytorch.org/whl/cpu
./.venv-qwen/bin/python -m pip install -r requirements.txt \
  'transformers==4.57.3' accelerate 'psycopg[binary]>=3.2,<4' 'pymilvus==2.6.18'
```

Qwen 模型目录须包含 `model.safetensors`、配置、分词器和图像预处理文件。补齐配套文件时执行：

```bash
./.venv-qwen/bin/hf download Qwen/Qwen3-VL-2B-Instruct \
  --local-dir .runtime/models --include '*.json' 'merges.txt'
```

已将场景和关键帧导入 PostgreSQL 时，先试跑一张图，再导入剩余帧。下面的连接串使用端口 5433 和示例账号 `scenes`，请使用之前导入场景时的连接参数和数据目录。每条命令会在终端提示输入数据库密码。

```bash
./.venv-qwen/bin/python index_semantics.py --limit 1 --device cpu \
  --database-url 'host=127.0.0.1 port=5433 dbname=scenes user=scenes' --ask-db-password \
  --milvus-uri http://127.0.0.1:19530 --dataset /data/hdd1/zh_data/my/code/v1 \
  --qwen-dir /data/hdd1/zh_data/my/code/.runtime/models

./.venv-qwen/bin/python index_semantics.py --device cpu \
  --database-url 'host=127.0.0.1 port=5433 dbname=scenes user=scenes' --ask-db-password \
  --milvus-uri http://127.0.0.1:19530 --dataset /data/hdd1/zh_data/my/code/v1 \
  --qwen-dir /data/hdd1/zh_data/my/code/.runtime/models

./.venv-qwen/bin/python app.py --host 127.0.0.1 --port 8000 \
  --database-url 'host=127.0.0.1 port=5433 dbname=scenes user=scenes' --ask-db-password \
  --milvus-uri http://127.0.0.1:19530 --dataset /data/hdd1/zh_data/my/code/v1
```

Qwen 的加载精度可通过 `--precision` 指定。`auto` 在 CPU 上使用 FP32，在 CUDA 上优先使用原生 BF16，其次使用 FP16。CUDA 显存紧张时，在运行脚本的 Python 环境中安装 `bitsandbytes`，将导入命令里的 `--device cpu` 改成 `--device cuda --precision int4`。脚本会在加载前打印所选设备和精度。

查询页填写「画面中有行人」可做语义查询，再勾选急刹或填写时段、路口即可做混合查询。API 示例：

```bash
curl -fsS -G http://127.0.0.1:8000/api/scenes --data-urlencode 'description=画面中有行人'
curl -fsS -G http://127.0.0.1:8000/api/scenes --data-urlencode 'description=画面中有车辆' --data 'brake=1'
```

Qwen3-VL-2B-Instruct 离线识别最多三项主要目标，描述类型及位置。导入时合并重复目标，按可辨别的车型生成类别。新版使用 Milvus 集合 `nuscenes_cam_front_qwen_objects`，从旧版升级时运行语义导入脚本生成此集合。重复运行会跳过已写入新版集合的帧。

搜索「人」或「车」时按对象类别匹配。「车」覆盖自行车、公交车、轿车等车辆子类，搜索「自行车」时只匹配含自行车类别的帧。「私家车」按乘用车外观类别匹配，例如轿车、SUV、MPV。其它描述用中文分词和 BM25 检索。混合查询先筛选符合时空条件的场景，再在候选关键帧中匹配，每个场景保留一帧，最多返回 10 个场景。
