# Windows 環境設置指南

本文件說明如何在 Windows 上設定並執行 iRadar。

## 前置需求

1. **iRacing**：確保已安裝並可正常執行
2. **iRacing 顯示模式**：必須設為 **Borderless Windowed**（無邊框視窗模式）
   - iRacing 設定 → Graphics → 取消勾選 "Full Screen"
   - 取消勾選 "Border"

## 安裝步驟

### 方法一：下載 .exe（推薦，不需要安裝 Python）

1. 到 GitHub repo 的 **Releases** 頁面
2. 下載最新版本的 `iRadar.exe`
3. 放到任意資料夾（例如桌面）
4. 雙擊即可執行

### 方法二：從原始碼執行

需要先安裝 **Python 3.8+**（從 [python.org](https://www.python.org/downloads/) 下載，安裝時勾選 "Add Python to PATH"）

```powershell
# 1. Clone 或下載專案
git clone https://github.com/YOUR_USERNAME/ir_radar.git
cd ir_radar

# 2. 建立虛擬環境（建議）
python -m venv venv
.\venv\Scripts\activate

# 3. 安裝依賴
pip install -r requirements.txt

# 4. 執行
python main.py
```

## 使用流程

### 首次使用

1. 啟動 iRacing（Borderless Windowed 模式）
2. 進入任何練習/比賽場次
3. 啟動 iRadar：`python main.py` 或雙擊 `iRadar.exe`
4. 雷達會顯示 "Recording track: XX%"
5. 完整跑完一圈 → 賽道資料自動儲存
6. 雷達開始正常顯示周圍車輛

### 日常使用

1. 啟動 iRacing
2. 啟動 iRadar（順序不重要）
3. iRadar 會自動連接到 iRacing
4. 已錄製過的賽道會直接載入，無需重新錄製

### 調整雷達位置

- 按住 **Alt** 鍵，然後用**左鍵拖曳**雷達到想要的位置
- 位置會自動儲存

## 設定調整

編輯 `config.json`（與 `main.py` 同目錄，首次執行後自動產生）：

```json
{
  "radar_size": 280,
  "range_metres": 40.0,
  "active_opacity": 0.92,
  "inactive_opacity": 0.25,
  "danger_distance": 8.0,
  "lateral_estimate": 3.5,
  "update_fps": 30
}
```

修改後重新啟動 iRadar 即可生效。

## 常見問題

### Q: 雷達沒有顯示在 iRacing 上面？
A: 確認 iRacing 是以 **Borderless Windowed** 模式執行，不是全螢幕。

### Q: 顯示 "Waiting for iRacing..."？
A: iRacing 尚未啟動或尚未進入場次。進入練習/比賽後會自動連接。

### Q: 賽道顯示 "Recording track"？
A: 這是正常的，表示這是第一次在這個賽道使用。跑完一圈就會自動完成錄製。

### Q: 想重新錄製某個賽道？
A: 刪除 `track_data/` 資料夾中對應的 `.json` 檔案，下次進入該賽道時會重新錄製。

### Q: 雷達太大/太小？
A: 用命令列參數 `--size 320` 或編輯 `config.json` 中的 `radar_size`。

### Q: 雷達範圍要調整？
A: 用命令列參數 `--range 50` 或編輯 `config.json` 中的 `range_metres`。

## 從 Mac 轉移到 Windows

最簡單的方式：到 GitHub Releases 下載 `iRadar.exe`，不需要轉移任何檔案。

如果要從原始碼執行：
1. 在 Windows 上 `git clone` 專案
2. 安裝 Python 3.8+
3. `pip install -r requirements.txt`
4. `python main.py`

`track_data/` 資料夾中的賽道快取可以跨平台使用。

## 技術架構

```
iRacing 記憶體映射
       ↓
pyirsdk (60Hz 讀取)
       ↓
telemetry.py → TelemetrySnapshot
       ↓
track_spline.py → CarIdxLapDistPct → (x,y) 座標
       ↓
radar_calc.py → RadarBlip 列表
       ↓
overlay.py → PyQt5 透明視窗渲染
```
