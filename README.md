# CICD App – mini CI/CD cho Linux Mint và Windows

App nhỏ chạy trên máy của bạn. Mỗi khi bạn đẩy phiên bản mới lên **GitHub** hoặc **Bitbucket** (cả Bitbucket Server / Data Center của công ty),
CICD App sẽ tự động:

1. **Lấy mã nguồn** về thư mục project trên máy (`git clone` lần đầu, các lần sau `git fetch` + `reset --hard`).
2. **Chuẩn bị môi trường Python**: tạo `.venv` trong thư mục project, cài thư viện từ `requirements.txt`.
3. Chạy các **lệnh build/test** của bạn (tuỳ chọn).
4. **Deploy – chạy app**: dừng phiên bản cũ, chạy phiên bản mới ở chế độ nền và kiểm tra app đã sẵn sàng.

Toàn bộ quá trình có **log trực tiếp** trên web. Khi lỗi, app chỉ rõ **lỗi ở bước nào**, hiện các dòng log cuối của bước đó và **gợi ý cách sửa**.

```
GitHub / Bitbucket ──(kiểm tra mỗi 60s, hoặc webhook)──► CICD App
                                                            │
   [1] Lấy mã nguồn ► [2] Môi trường Python ► [3..] Lệnh build/test ► [cuối] Deploy: chạy app
                                                            │
                    Web UI http://<IP bất kỳ của máy>:8088  (projects, lịch sử run, log, bật/tắt app)
```

---

## 1. Cài đặt

### Windows

Cần cài trước: **[Git for Windows](https://git-scm.com/download/win)** và **[Python 3.10+](https://www.python.org/downloads/)**
(khi cài Python nhớ tick *Add python.exe to PATH*).

**Double-click `run.bat`.** Lần đầu, nó tự tạo môi trường Python (`.venv`), cài thư viện rồi mở trình duyệt.
Các lần sau, nó chỉ cài lại khi `requirements.txt` thay đổi.

- Dừng CICD App: đóng cửa sổ đen (hoặc Ctrl+C). Các app đã deploy **vẫn chạy tiếp**.
- Đổi cổng: `run.bat 8888` (hoặc `run.bat --port 8888`). Không ghi cổng thì dùng 8088.
- Chạy nền, không cần cửa sổ đen: `cicd.bat start 8888`, `cicd.bat status`, `cicd.bat stop`, `cicd.bat restart`
  (giống lệnh `cicd` trên Linux bên dưới; chạy `run.bat` một lần trước để tạo `.venv`).
- Tự chạy khi đăng nhập Windows: `Win+R` → `shell:startup` → tạo shortcut tới `run.bat` trong thư mục vừa mở.
- Dữ liệu: `%LOCALAPPDATA%\cicd-app` (database, log của run, log của app, khoá mã hoá).

### Linux Mint

Có hai cách chạy. **Chỉ chọn một**, vì cả hai cùng dùng cổng của CICD App.

#### Cách A – lệnh `cicd` (bật/tắt bằng tay, đổi cổng dễ)

```bash
cd ~/cicd-app
chmod +x cicd.sh
./cicd.sh install       # lần đầu: tạo .venv, cài thư viện, tạo lệnh `cicd` trong ~/.local/bin
```

Sau đó gõ ở bất kỳ thư mục nào:

| Lệnh | Tác dụng |
|---|---|
| `cicd start` | Chạy nền ở **cổng mặc định 8088**. Chờ tới khi web phản hồi rồi in địa chỉ |
| `cicd start 8888` | Chạy ở cổng 8888 (cũng nhận `--port 8888`, `-port 8888`, `-p 8888`) |
| `cicd start 8888 --host 127.0.0.1` | Chỉ cho chính máy này truy cập (mặc định: mọi địa chỉ IP của máy) |
| `cicd stop` | Dừng CICD App. Các app đã deploy **vẫn chạy** |
| `cicd restart` | Dừng rồi chạy lại, **giữ cổng đang dùng** |
| `cicd restart 9999` | Chạy lại ở cổng 9999 |
| `cicd status` | Đang chạy hay không, PID, cổng, địa chỉ, thời gian chạy, bộ nhớ (mã thoát 3 = đã dừng) |
| `cicd logs` / `cicd logs -f` | Xem log của CICD App / theo dõi liên tục (Ctrl+C để thoát) |

- `cicd` tự cài lại thư viện khi `requirements.txt` thay đổi (vd. sau khi cập nhật code CICD App).
- Cổng đã bị chiếm: `cicd start` báo chương trình nào đang dùng cổng và gợi ý cổng khác.
- Đang chạy ở cổng 8888 mà gõ `cicd start 9999`: lệnh nhắc dùng `cicd restart 9999`.
- Nếu `cicd` báo không tìm thấy lệnh, thêm `export PATH="$HOME/.local/bin:$PATH"` vào `~/.profile` rồi mở terminal mới.
  Hoặc gọi trực tiếp `~/cicd-app/cicd.sh start 8888`.
- Log: `~/.local/share/cicd-app/cicd-app.log`.

#### Cách B – dịch vụ systemd (tự chạy khi khởi động máy)

```bash
cd ~/cicd-app
chmod +x install.sh
./install.sh                    # KHÔNG chạy bằng sudo; script sẽ tự hỏi sudo khi cần
CICD_PORT=9000 ./install.sh     # cài với cổng khác
CICD_HOST=127.0.0.1 ./install.sh  # chỉ cho chính máy này truy cập
```

`install.sh` cài `git` và `python3-venv`, rồi tạo dịch vụ systemd của user. Dịch vụ tự chạy khi khởi động máy, kể cả khi chưa đăng nhập.

```bash
systemctl --user start|stop|restart|status cicd-app
journalctl --user -u cicd-app -f      # log của CICD App
```

Muốn chuyển từ cách B sang cách A: `systemctl --user stop cicd-app && systemctl --user disable cicd-app`.
`cicd start` sẽ báo nếu dịch vụ vẫn đang chạy. Nếu đã cài bằng `install.sh` bản cũ, hãy chạy lại `./install.sh` một lần
để thêm `KillMode=process`. Khi đó restart dịch vụ sẽ không làm dừng các app đã deploy.

#### Chạy trực tiếp ở cửa sổ terminal (thử nhanh, Ctrl+C để dừng)

```bash
.venv/bin/python run.py                 # cổng mặc định 8088
.venv/bin/python run.py 8888            # cổng 8888; cũng nhận --port 8888, -port 8888, -p 8888, --port=8888
.venv/bin/python run.py 8888 --host 127.0.0.1   # chỉ máy này
```

Dữ liệu (cả 3 cách dùng chung): `~/.local/share/cicd-app/`.

### Truy cập từ máy khác (mọi địa chỉ IP của máy)

CICD App **mặc định lắng nghe trên mọi địa chỉ IP của máy** (`0.0.0.0`). Ví dụ máy có hai địa chỉ `192.168.8.33` và
`10.0.0.33` thì máy khác trong mạng mở được bằng cả `http://192.168.8.33:8088` và `http://10.0.0.33:8088`.
Máy có nhiều card mạng hơn (LAN, Wi-Fi, VPN…) thì mở được bằng mọi địa chỉ của chúng. Trên chính máy đó vẫn mở bằng
`http://127.0.0.1:8088`.

Danh sách địa chỉ hiện ở:
- log khi khởi động (cửa sổ `run.bat`, `cicd logs`, `journalctl`),
- `cicd start` / `cicd status`,
- trang **⚙ Cài đặt → Địa chỉ truy cập CICD App**.

Chỉ cho chính máy này truy cập: thêm `--host 127.0.0.1` (`run.py`, `cicd start`), hoặc đặt biến `CICD_HOST=127.0.0.1`
(`run.bat`, `install.sh`).

**Mở firewall** cho cổng của CICD App nếu máy khác không vào được (chỉ làm một lần):

```bash
# Linux Mint (chỉ cần nếu đã bật ufw)
sudo ufw allow 8088/tcp
```
```powershell
# Windows - PowerShell chạy bằng quyền Administrator (chỉ cho các máy trong cùng mạng LAN)
New-NetFirewallRule -DisplayName "CICD App (TCP 8088)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8088 -Profile Private,Public -RemoteAddress LocalSubnet
```
Trên Windows, lần đầu chạy có thể hiện hộp thoại *Windows Security Alert* hỏi cho phép `python.exe` truy cập mạng. Chọn **Allow**.

### Lần đầu mở web

Lần đầu, web yêu cầu tạo **mật khẩu quản trị**. Để an toàn, bước này **chỉ làm được trên chính máy chạy CICD App**
(mở `http://127.0.0.1:8088`). Máy khác mở vào lúc đó sẽ thấy thông báo hướng dẫn. Sau khi đã có mật khẩu,
mọi máy trong mạng đăng nhập được bằng mật khẩu đó.

---

## 2. Token đăng nhập (thay cho mật khẩu)

> ⚠ **GitHub và Bitbucket không cho dùng mật khẩu tài khoản để kéo code qua git.** Hãy tạo **token** chỉ có quyền **đọc**
> repository rồi nhập vào ô *Token*. Repo public thì để trống token. Token được mã hoá khi lưu và luôn bị che `****` trong log.

### GitHub – Fine-grained Personal Access Token
1. GitHub → ảnh đại diện → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. *Repository access*: **Only select repositories**, rồi chọn repo.
3. *Repository permissions*: **Contents: Read-only**.
4. Trên form: Nhà cung cấp **GitHub**, Kiểu xác thực **Username + token**, Username = username GitHub, Token = `github_pat_...`.

### Bitbucket Server / Data Center (vd. `https://bbk-rndsoft.ingenico.com`)
Dùng **HTTP access tokens**. **Không dùng "Access keys"**: đó là SSH key, còn CICD App kết nối qua HTTPS.

| Token tạo ở đâu | Kiểu xác thực trên form | Username |
|---|---|---|
| **Repository settings → HTTP access tokens** (khuyên dùng, chỉ đọc được 1 repo) | **Bearer token** | username bbk (chỉ dùng để tạo URL `~username`) |
| Project settings → HTTP access tokens | **Bearer token** | username bbk |
| Ảnh đại diện → Manage account → HTTP access tokens (token cá nhân) | **Username + token** | username bbk |

- Quyền cần chọn: **Repository read**. Token chỉ hiện một lần, hãy copy ngay.
- URL repo có dạng `https://<server>/scm/~<username>/<repo>.git` (repo cá nhân) hoặc `https://<server>/scm/<PROJECT>/<repo>.git`.
- Chế độ Bearer cần git ≥ 2.31 (Linux Mint 21+ và Git for Windows bản mới đều đáp ứng).

> ⚠ **Chọn sai kiểu xác thực là lỗi hay gặp nhất.** Token tạo trong *Repository settings* mà chọn *Username + token* thì mỗi lần
> kết nối đều bị tính là **đăng nhập sai mật khẩu**, và Bitbucket sẽ **khoá tài khoản** (đòi CAPTCHA). Xem mục 6.

### Bitbucket Cloud (bitbucket.org)
API token: **id.atlassian.com → Security → API tokens → Create API token with scopes**, chọn Bitbucket, quyền `read:repository:bitbucket`.
Trên form: Kiểu xác thực **Username + token**, Username = `x-bitbucket-api-token-auth`.

Sau khi tạo project, luôn bấm **🔌 Kiểm tra kết nối** để chắc chắn token và URL đúng.

---

## 3. Tạo project

**Bước 1 – Cài đặt (một lần):** vào **⚙ Cài đặt** và điền:
- **Thư mục chứa project mặc định**, vd. `C:\02-Study\projects` (Windows) hoặc `/home/ten-ban/apps` (Linux). Có thể bấm **📁 Chọn thư mục**.
- **Bitbucket Server URL** (`https://bbk-rndsoft.ingenico.com`) và **Username bbk**. Khi có hai giá trị này, URL repo được tự điền.

**Bước 2 – Tạo project:** mỗi repository là một project. Bấm **+ Tạo project**:

| Mục | Ý nghĩa |
|---|---|
| **1. Tên project** | **Duy nhất** (trùng tên, kể cả khác chữ hoa/thường, sẽ báo lỗi). Chỉ gồm chữ cái không dấu, số, `-` `_` `.` vì tên này cũng là tên thư mục |
| Nhà cung cấp, URL, Nhánh | GitHub / Bitbucket Cloud / Bitbucket Server. Với Bitbucket Server, URL tự điền `…/scm/~<username>/<tên project>.git` và vẫn sửa được |
| **2. Đăng nhập** | Kiểu xác thực, Username, Token: xem mục 2 |
| **3. Thư mục chứa project** | App **tự tạo thư mục con mang tên project** bên trong: `C:\02-Study\projects` + `sieucp` → **`C:\02-Study\projects\sieucp`**. Form hiện sẵn đường dẫn để xem trước |
| **4. Python** | Bật sẵn: tạo `.venv` và cài thư viện (mục 4) |
| Lệnh build/test | Mỗi dòng một bước, vd. `pytest`. Để trống nếu không cần |
| **5. Deploy – chạy app** | Lệnh chạy app (mục 5). **Để trống thì app sẽ không được chạy** |
| **6. Tự động hoá** | Kiểm tra commit mới mỗi N giây (0 = tắt), timeout mỗi bước, bật/tắt tự động chạy |

Về thư mục project:
- Chưa có hoặc trống: `git clone` vào đó.
- Đã là git repo: `git fetch` rồi `reset --hard`. Sửa đổi chưa commit trên **file thuộc repo** sẽ bị ghi đè. File git không quản lý
  (`.env`, `telegram.env`, `data/`, `.venv`…) **được giữ nguyên**.
- Có file nhưng không phải git repo: báo lỗi, để tránh ghi đè nhầm.

**Lần chạy đầu tiên:** nếu project bật *tự động chạy*, CICD App **tự bắt đầu lần chạy đầu trong vòng vài giây** sau khi tạo,
vì project chưa lấy commit nào. Trang project sẽ tự hiện **khung tiến trình** của lần chạy này.
Nếu bạn bấm **▶ Chạy ngay** trong lúc đó, app mở luôn trang tiến trình của run đang chạy, không tạo run mới.
Lần đầu thường lâu hơn (clone code, tạo `.venv`, cài toàn bộ thư viện). Các lần sau chỉ cập nhật phần thay đổi.
- Hai project không được dùng chung thư mục hoặc thư mục lồng nhau. Đổi tên project hoặc thư mục chứa thì thư mục cũ sẽ được
  **di chuyển** sang vị trí mới. Phải tắt app trước khi làm việc này.

---

## 4. Môi trường Python (`.venv`)

Ngay sau bước lấy code, app chạy bước **Môi trường Python (.venv + thư viện)**:

1. Tạo `<thư mục project>/.venv` nếu chưa có. Nếu `.venv` bị hỏng hoặc bạn đổi Python, app tạo lại.
2. Cài thư viện từ `requirements.txt` (hoặc `pyproject.toml` / `setup.py`). **Nếu file không đổi thì bỏ qua**, nên bước này chỉ mất khoảng 1 giây.
3. Các bước sau, kể cả Deploy, đều chạy **trong `.venv`**, nên chỉ cần viết `python …`.

Mặc định `.venv` được tạo bằng Python đang chạy CICD App. Muốn dùng phiên bản khác thì điền ô *Python dùng để tạo .venv*,
vd. `py -3.11` (Windows) hoặc `python3.11` (Linux).

---

## 5. Deploy – chạy app

Đây là bước cuối của pipeline, chạy sau khi đã cập nhật code và môi trường.

**Chạy nền liên tục** (web app, Streamlit, FastAPI, bot Telegram…). Mỗi dòng trong ô *Lệnh chạy app* là một tiến trình.
Mỗi lần deploy, app sẽ:
1. dừng phiên bản cũ, kể cả các tiến trình con,
2. chạy phiên bản mới trong `.venv`, output ghi vào **log riêng của app**,
3. theo dõi N giây và chờ **URL kiểm tra** phản hồi (nếu có điền).

Nếu app thoát ngay, deploy báo **thất bại** kèm log lỗi của app và gợi ý (thiếu thư viện, port bị chiếm, thiếu token…), đồng thời
dừng các tiến trình còn lại. App **vẫn chạy tiếp** khi pipeline kết thúc hoặc khi đóng CICD App. Nếu bật *Tự chạy lại app khi
CICD App khởi động*, app sẽ chạy lại sau khi khởi động lại máy.

**Chạy 1 lần**: dành cho script hoặc job. App chạy lệnh và chờ kết thúc; output nằm trong log của run.
Lệnh khởi động server (uvicorn, streamlit run, flask run, runserver…) **không được** dùng ở chế độ này, vì nó không bao giờ kết thúc
và sẽ treo pipeline. Form sẽ báo lỗi nếu bạn chọn như vậy.

**Tự nhận diện lệnh chạy app:** nếu để trống ô *Lệnh chạy app* (và giữ tick *tự nhận diện*), CICD App đọc mã nguồn để chọn lệnh.
Việc nhận diện diễn ra khi lưu form (nếu thư mục đã có code), ở lần chạy pipeline đầu tiên, hoặc khi bấm **▶ Bật app**.
App tự chọn port còn trống (không trùng project khác) và URL kiểm tra:

| Nhận ra | Lệnh được điền | URL kiểm tra |
|---|---|---|
| FastAPI (`app/main.py`, `main.py`…) | `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` | `/health` (nếu có route), không thì `/docs` |
| Streamlit | `python -m streamlit run app.py --server.port=8501 --server.headless=true …` | `/_stcore/health` |
| Flask | `python -m flask --app app:app run --host 127.0.0.1 --port 5000` | `/` |
| Django | `python manage.py runserver 127.0.0.1:8000 --noreload` | `/` |
| Script có `if __name__ == "__main__"` | `python main.py` | (không) |

Lệnh được nhận diện vẫn sửa được trong ✎ Sửa → mục 5.

Khi `requirements.txt` thay đổi, app được dừng **ngay trước** khi cài thư viện, vì Windows khoá file trong `.venv` khi app đang chạy.
Nếu requirements không đổi, app chỉ gián đoạn vài giây lúc chuyển sang phiên bản mới.

### Ví dụ

| App | Lệnh chạy app | URL kiểm tra |
|---|---|---|
| **sieucp** (Streamlit) | `python -m streamlit run app.py --server.port=8899 --server.headless=true --browser.gatherUsageStats=false` | `http://127.0.0.1:8899/_stcore/health` |
| sieucp – bot Telegram (dòng thứ 2) | `python telegram_bot.py` (cần file `Phong_telegram.env` trong thư mục project) | |
| **accstock** (FastAPI) | `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` | `http://127.0.0.1:8000/health` |
| accstock trên Linux (ép giờ Việt Nam) | `TZ=Asia/Ho_Chi_Minh python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` | `http://127.0.0.1:8000/health` |

Lưu ý:
- **Không dùng** các launcher có `pause`, tự mở trình duyệt, tự cài thư viện (`run.bat` của project), hoặc tự chạy ngầm rồi thoát
  (`accstock start`, `sieucp start`). CICD App sẽ tưởng app đã dừng.
- Mỗi app dùng một **port riêng**. Đừng chạy cùng app ngoài CICD App (VS Code, terminal, autostart riêng của project), nếu không deploy sẽ báo port bị chiếm.
- `--host 127.0.0.1` thì chỉ máy này mở được app. Đổi thành `0.0.0.0` để mở từ máy khác trong mạng (nhớ mở firewall cho port đó).
- File bí mật không nằm trong git (`.env`, `telegram.env`, `Phong_telegram.env`) phải copy tay vào thư mục project **một lần**.

### Chạy app bằng tay (ngoài CICD App)

```
# Windows (cmd / PowerShell), trong thư mục project
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Linux
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Nếu app đang được CICD App chạy thì bấm **■ Tắt app** trước, để tránh trùng port.

---

## 6. Sử dụng hằng ngày

### Trang chủ (Projects)
**Mọi project** đều có cột **App** cho biết trạng thái `● đang chạy` / `○ đã dừng` / `○ chưa có lệnh`, kèm nút
**■ Tắt app** / **▶ Bật app**, và nút **▶ Chạy pipeline** (lấy code mới, cài môi trường, chạy lại app).

Bấm **▶ Bật app** khi project chưa có lệnh: app tự nhận diện lệnh rồi chạy. Nếu không nhận diện được, form mở ở mục 5 để bạn nhập.
Nếu project đang ở chế độ *Chạy 1 lần* với lệnh server, app tự chuyển sang *Chạy nền liên tục*.

**▶ Chạy pipeline** mở trang tiến trình của run vừa bắt đầu, hoặc của run đang chạy nếu project đang bận.
Khi có project đang chạy, trang chủ tự làm mới mỗi 5 giây. Bấm badge `running` để xem tiến trình.

### Trang project
- **▶ Chạy ngay**: khi có run đang chạy, nút này đổi thành **⏳ Đang chạy - xem tiến trình**.
- **Khung tiến trình trực tiếp**: tự hiện khi project có run đang chạy, kể cả run do bộ lập lịch tự khởi động (commit mới, lần chạy đầu).
  Khung có thanh tiến độ, dòng *Bước X/Y: tên bước*, 12 dòng log mới nhất và nút *Xem log đầy đủ →*.
  Chạy xong, trang tự tải lại để cập nhật lịch sử và trạng thái app.
- **■ Tắt app / ▶ Bật app** trên đầu trang. Khung **App** có PID, thời gian chạy, nút **↻ Khởi động lại** và **📄 Log app** (tự cập nhật).
- **🔌 Kiểm tra kết nối**, **📂 Mở thư mục** (mở bằng Explorer/Nemo trên máy chạy CICD App), **📋 Copy đường dẫn**.
- **Lịch sử run**: trạng thái, commit, nguồn kích hoạt, thời gian, *lỗi tại bước nào*.

### Trang run (tiến trình của một lần chạy)
- **Thanh tiến độ**: *⏳ Bước X/Y: tên bước đang chạy*, số bước đã xong và thời gian đã chạy.
  Kết thúc thì hiện *✔ Hoàn thành* hoặc *✖ Thất bại tại …*.
- **Bảng các bước** (`success` / `failed` / `skipped`, exit code, thời gian) và **log trực tiếp**, có tuỳ chọn tự cuộn xuống cuối.
- Các bước im lặng lâu có dòng báo trước, ví dụ *"Đang tạo .venv - thường mất 10-30 giây…"* hoặc *"Cài thư viện… lần đầu có thể
  mất vài phút"*. Output của `pip` hiện ngay khi nó chạy.
- Khi thất bại, khung đỏ hiển thị **bước lỗi**, **~40 dòng log cuối** và **Gợi ý**.

### Chống khoá tài khoản khi đăng nhập sai
Nếu đăng nhập repository thất bại (token sai, hết hạn, chọn sai kiểu xác thực, tài khoản bị khoá), CICD App **tự tắt tự động chạy**
cho project đó và **không thử lại**. Nếu cứ thử lại mỗi 60 giây, Bitbucket sẽ khoá tài khoản. Trang project hiện khung ⛔ và nút
**▶ Bật lại tự động**.

Cách xử lý:
1. Bấm **✎ Sửa**, kiểm tra Kiểu xác thực và Token, rồi Lưu.
2. Nếu tài khoản bị khoá (lỗi `CAPTCHA required`): mở Bitbucket trên trình duyệt, **đăng xuất rồi đăng nhập lại** và giải CAPTCHA.
3. Bấm **🔌 Kiểm tra kết nối**. Thành công thì bấm **▶ Bật lại tự động**.

---

## 7. Lỗi thường gặp

| Hiện tượng / log | Nguyên nhân và cách sửa |
|---|---|
| Run chỉ có bước 1 và 2, app không chạy | Ô **Lệnh chạy app** đang trống và không tự nhận diện được. Trang project hiện khung "ℹ Chưa cấu hình chạy app" → ✎ Sửa → mục 5 |
| Bấm Chạy ngay nhưng không thấy gì chạy | Project đang có run khác (vd. lần chạy đầu do app tự khởi động). Bản mới sẽ tự mở trang tiến trình của run đó; nếu vẫn không thấy, hãy mở lại `run.bat` để dùng bản mới |
| Bước Deploy "chạy 1 lần" treo ở `running` rồi lỗi timeout | Lệnh là server chạy liên tục. Chọn **Chạy nền liên tục**, hoặc bấm ▶ Bật app để app tự chuyển |
| `CAPTCHA required` / `account has been locked` | Tài khoản Bitbucket bị khoá do đăng nhập sai nhiều lần. Xem mục 6 |
| `Authentication failed` | Token sai, hết hạn hoặc thiếu quyền. Với Bitbucket Server, kiểm tra **Bearer** hay **Username + token** |
| `Repository not found` | URL sai, hoặc token chưa có quyền với repo private |
| `SSL certificate problem` | Máy không tin chứng chỉ của server nội bộ. Windows: `git config --global http.sslBackend schannel`. Linux: cài file CA vào `/usr/local/share/ca-certificates/` rồi chạy `sudo update-ca-certificates` |
| `port … already in use` / `10048` | App đang chạy ở nơi khác (VS Code, terminal, autostart riêng). Tắt nó rồi bấm Khởi động lại |
| `ModuleNotFoundError` | Thiếu gói trong `requirements.txt` |
| `TELEGRAM_BOT_TOKEN is missing` | Chưa copy file `.env` chứa token vào thư mục project |
| `ensurepip is not available` (Linux) | `sudo apt install python3-venv` |
| Lỗi pip khi app đang chạy (Windows) | File `.venv` bị khoá. CICD App đã tự dừng app trước khi cài; nếu vẫn lỗi, bấm **■ Tắt app** rồi chạy lại pipeline |
| exit code `127` / `124` | Lệnh không tồn tại / bước chạy quá timeout |

Log thô: nút *Xem log dạng text* trên trang run, hoặc thư mục dữ liệu (`logs/run_<id>.log`, `applogs/project_<id>_<n>.log`).

---

## 8. Khác

### `.cicd.yml` trong repo
Thay vì gõ lệnh build/test trên web, có thể đặt file **`.cicd.yml`** ở gốc repo (xem `.cicd.yml.example`). File này được ưu tiên
hơn ô lệnh trên web.

```yaml
env:
  APP_ENV: production
steps:
  - name: Test
    run: pytest -q
    timeout: 600
```
Mỗi bước chạy bằng `bash -lc` (trên Windows là Git Bash) với `set -eo pipefail`. Biến có sẵn: `CICD_COMMIT`, `CICD_BRANCH`,
`CICD_RUN_ID`, `CICD_PROJECT`, `CICD_DEPLOY_DIR`, `CICD_VENV`.

### Polling hay Webhook?
- **Polling** (mặc định, 60 giây): hoạt động cả khi máy ở sau router/NAT. Khuyên dùng.
- **Webhook**: chạy ngay khi push, nhưng server Git phải gọi được tới máy bạn. URL webhook hiển thị ở trang project.

### Bảo mật
- Web UI mặc định mở cho **mọi địa chỉ IP của máy** (máy khác trong mạng truy cập được). Mọi trang đều cần **mật khẩu quản trị**
  và có chống CSRF. Mật khẩu lần đầu chỉ tạo được trên chính máy chạy CICD App. Hãy đặt mật khẩu mạnh, hoặc dùng
  `--host 127.0.0.1` nếu không cần truy cập từ máy khác.
- Token mã hoá bằng `secret.key` trong thư mục dữ liệu. Mất file này thì phải nhập lại token.
- Git không bao giờ gửi username + mật khẩu khi dùng Bearer token hoặc khi không có token.
- Pipeline và app chạy bằng quyền của user đang chạy CICD App. Chỉ thêm repo mà bạn tin tưởng.

### Quên mật khẩu quản trị
Xoá dòng `admin_hash` trong bảng `settings` của `cicd.db` (thư mục dữ liệu), rồi khởi động lại CICD App để tạo mật khẩu mới:
```bash
sqlite3 ~/.local/share/cicd-app/cicd.db "DELETE FROM settings WHERE key='admin_hash';"
```
Trên Windows, file nằm ở `%LOCALAPPDATA%\cicd-app\cicd.db`. Dùng *DB Browser for SQLite* hoặc lệnh `sqlite3` tương tự.

---

## Cấu trúc mã nguồn

```
cicd-app/
├── run.bat                # Windows: chạy bằng 1 click (run.bat 8888 = đổi cổng)
├── run.py                 # điểm khởi động (python run.py [PORT] [--host H])
├── cicd.sh                # Linux: lệnh `cicd start|stop|restart|status|logs` (./cicd.sh install để cài)
├── cicd.bat               # Windows: lệnh tương tự (cicd.bat start 8888 ...)
├── cicd_cli.py            # phần xử lý của lệnh cicd: chạy nền, PID, cổng, kiểm tra sẵn sàng
├── install.sh             # Linux: cài đặt + dịch vụ systemd
├── requirements.txt
├── .cicd.yml.example      # mẫu pipeline đặt trong repo của bạn
└── cicd/
    ├── web.py             # web UI, đăng nhập, cài đặt, chọn thư mục, webhook, API log, nút bật/tắt app
    ├── pipeline.py        # lấy mã nguồn (git), chạy các bước, ghi log, gợi ý lỗi, chống khoá tài khoản
    ├── python_env.py      # tạo .venv và cài thư viện
    ├── app_runner.py      # deploy: chạy/dừng app nền, log app, tự khởi động lại
    ├── scheduler.py       # kiểm tra commit mới định kỳ
    ├── store.py           # SQLite
    ├── secrets_box.py     # mã hoá token
    ├── config.py
    └── templates/         # giao diện HTML
```
# cicd
