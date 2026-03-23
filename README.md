# iRadar — 360° Proximity Radar Overlay for iRacing

iRacing 沒有內建的雷達功能（不像 ACC / LMU），而現有的第三方 overlay（Kapps、Racelabs 等）只提供簡單的左右來車指示條。

**iRadar** 提供類似 ACC/LMU 的 360 度俯瞰式雷達，以自車為中心即時顯示周圍所有鄰近車輛的相對位置。

## 功能

- **360 度雷達圖**：圓形雷達顯示所有方向的鄰近車輛
- **即時更新**：60Hz 遙測資料，30fps 渲染
- **動態透明度**（方案 C）：沒車時半透明低調，有車靠近時自動變亮
- **顏色警示**：依距離漸變（遠→橘色，近→紅色，被套圈→藍色）
- **自動賽道學習**：首次進入賽道時自動錄製路徑，之後重複使用
- **透明 Overlay**：無邊框、置頂、滑鼠穿透，不影響操作
- **可拖曳定位**：Alt + 左鍵拖曳調整位置

## 技術原理

```
1. 玩家車輛的 Lat/Lon GPS 資料 → 建立賽道路徑 spline
2. 所有車輛的 CarIdxLapDistPct → 查 spline → (x, y) 世界座標
3. 計算相對於玩家的向量 → 以 Yaw 旋轉 → 雷達座標
4. CarLeftRight → 近車側向修正
5. PyQt5 透明視窗渲染雷達圖
```

## 快速開始（Windows 使用者）

### 方法一：下載 .exe（推薦）

1. 到 GitHub Releases 頁面下載最新的 `iRadar.exe`
2. 放到任意資料夾（例如桌面）
3. 啟動 iRacing（Borderless Windowed 模式）
4. 雙擊 `iRadar.exe`

不需要安裝 Python 或任何依賴。

### 方法二：從原始碼執行

#### 環境需求

- Python 3.8+
- Windows 10/11（執行時需要 iRacing）
- iRacing 必須以 **Borderless Windowed** 模式執行

#### 安裝

```bash
cd ir_radar
pip install -r requirements.txt
```

#### 執行

```bash
# Windows — 連接 iRacing
python main.py

# Mac / 開發 — 使用模擬資料
python main.py --mock

# 自訂參數
python main.py --mock -n 12 --size 320 --range 50
```

### 命令列參數

| 參數 | 說明 | 預設值 |
|------|------|--------|
| `--mock` | 使用模擬資料（無需 iRacing） | false |
| `-n, --num-opponents` | Mock 模式的對手數量 | 8 |
| `--size` | 雷達大小（像素） | 280 |
| `--range` | 雷達範圍（公尺） | 40 |
| `--fps` | 更新幀率 | 30 |

### 操作

- **Alt + 左鍵拖曳**：移動雷達位置
- 位置會自動儲存到 `config.json`

## 首次使用（賽道學習）

第一次在某個賽道使用時，iRadar 會顯示 "Recording track: XX%"。
請完整跑完一圈，跨過起終點線後賽道資料會自動儲存到 `track_data/` 資料夾。
之後再次進入同一賽道會直接載入快取。

如需重新錄製，刪除 `track_data/` 中對應的 `.json` 檔案即可。

## 設定

所有設定存放在 `config.json`（自動產生），可以手動編輯：

| 設定 | 說明 | 預設值 |
|------|------|--------|
| `radar_size` | 雷達大小（px） | 280 |
| `range_metres` | 顯示範圍（m） | 40 |
| `active_opacity` | 有車時透明度 | 0.92 |
| `inactive_opacity` | 無車時透明度 | 0.25 |
| `danger_distance` | 危險距離閾值（m） | 8 |
| `lateral_estimate` | 側向偏移估算值（m） | 3.5 |

## 專案結構

```
ir_radar/
├── main.py                 # 啟動入口
├── requirements.txt        # Python 依賴
├── iRadar.spec             # PyInstaller 打包設定
├── config.json             # 設定檔（自動產生，不進 git）
├── .github/
│   └── workflows/
│       └── build.yml       # GitHub Actions 自動打包 .exe
├── server/
│   ├── config.py           # 設定管理
│   ├── telemetry.py        # pyirsdk 遙測讀取
│   ├── mock_data.py        # Mac 開發用模擬資料
│   ├── track_spline.py     # 賽道路徑錄製與載入
│   ├── radar_calc.py       # 360° 雷達座標計算
│   └── overlay.py          # PyQt5 透明 overlay 視窗
├── track_data/             # 賽道路徑快取
└── docs/
    └── WINDOWS_SETUP.md    # Windows 環境設置指南
```

## 自動打包（GitHub Actions）

每次推送 `v*` 標籤（例如 `v1.0.0`）到 GitHub，Actions 會自動在 Windows 環境打包成 `iRadar.exe`，並上傳到 GitHub Releases。

```bash
git tag v1.0.0
git push origin v1.0.0
```

也可以手動觸發：GitHub repo → Actions → Build iRadar.exe → Run workflow。

### 手動打包

```bash
pip install pyinstaller
pyinstaller iRadar.spec
```

產出的 `dist/iRadar.exe` 可直接雙擊使用。

## 授權

MIT License
