# Recruitment UI — bàn giao cho team

Module `aho_recruitment_ui` cung cấp giao diện sáng cho Odoo Recruitment: hai nhóm điều hướng, sidebar thu gọn, card tuyển dụng, card/list ứng viên và lịch sử phỏng vấn AI trong hồ sơ. Baseline main: `f444ec8`. Bản đồng bộ token/component theo kit Figma đã được kiểm tra lại trên Odoo ngày 31/10/2026; xem phạm vi runtime bên dưới.

## Bộ UI chung để team phát triển tiếp

Điểm bắt đầu cho mọi màn hình mới là addon này. Bộ UI hiện tại dùng Odoo 20 / OWL 3 và CSS/SCSS, theo phong cách sáng của kit Venture CRM; không cần cài thêm thư viện giao diện để chỉnh tiếp trong Odoo.

| Muốn chỉnh | Nguồn dùng chung | Quy ước |
| --- | --- | --- |
| Màu, font, khoảng cách, radius, độ rộng sidebar | [design_system.css](static/src/css/design_system.css) | Token `--aho-*` chỉ định nghĩa tại đây. Chú thích `kit` là giá trị đọc từ Figma; `approx` là giá trị ước lượng, có thể tinh chỉnh. |
| Nút, badge, card, bảng, form, tab, header, cột Kanban | [workspace_components.scss](static/src/css/workspace_components.scss) | Dùng cấu trúc/class Odoo sẵn có; sửa component một lần để các trang nhận cùng kiểu. |
| Bố cục và điều hướng | [material_sidebar.scss](static/src/css/material_sidebar.scss), [JS](static/src/js/material_sidebar.js), [XML](static/src/xml/material_sidebar.xml) | Chỉ đặt style điều hướng/layout ở đây; menu đi qua menu/action service và quyền Odoo. |
| Funnel giai đoạn và danh sách cảnh báo | [stage_funnel.js](static/src/js/stage_funnel.js), [stage_funnel.xml](static/src/xml/stage_funnel.xml) | Tái sử dụng field widget `aho_stage_funnel`, `aho_warning_list`; dữ liệu và quyết định nghiệp vụ nằm ở model. |
| Card vị trí và profile/list/card ứng viên | [recruitment.xml](views/recruitment.xml), [applicant.xml](views/applicant.xml) | Kế thừa view core, giữ action, filter, drag/drop, field và quyền truy cập. |
| Trang phỏng vấn của ứng viên | [templates.xml](views/templates.xml), phần `body.aho-public` trong token CSS | Cùng token với workspace; layout public có scope riêng. |
| Logo | [ai-hiring-operator.svg](static/src/img/ai-hiring-operator.svg) | Tái sử dụng asset này ở các điểm nhận diện. |

### Cách thêm hoặc sửa một màn hình

1. Fetch main mới nhất và tạo nhánh riêng. Với thay đổi giao diện, chỉnh trong `aho_recruitment_ui`; tránh sửa trực tiếp addon core/AI chỉ để đổi style.
2. Tái sử dụng nút native `btn btn-primary` / `btn btn-secondary`, badge và cấu trúc form/list/Kanban có sẵn. Kiểu dùng chung chỉ đặt trong `workspace_components.scss`; style mới đặt dưới scope workspace hiện có, dùng token thay vì tạo bảng màu/radius riêng cho từng trang.
3. Dùng tiền tố `aho-` cho class mới, `--aho-*` cho token mới. Kiểm tra thành phần hiện có trước khi thêm biến thể. Giữ cả trạng thái hover, focus, disabled, empty, loading và error khi thành phần có những trạng thái này.
4. Khi thêm file, khai báo đúng thứ tự asset trong `__manifest__.py`: token trước style component. Màn Odoo ngoài workspace có sidebar không tự nhận toàn bộ style; cần thống nhất scope và kiểm tra lại trước khi mở rộng.
5. Nâng cấp addon khi đổi model/view/manifest, tải lại assets và kiểm tra trên đúng database thử. Với màn có hành vi, kiểm tra card/list, lọc theo vị trí, mở profile, thao tác Kanban và quyền người dùng; thêm kiểm tra riêng cho hành vi mới.
6. So lại desktop và cửa sổ hẹp, sidebar mở/thu gọn, chữ dài, hồ sơ rỗng và lỗi. Chụp màn đã kiểm tra trong PR để người tiếp theo có mốc đối chiếu.

### Khi teammate nối hub AI

Nếu hub chạy trong Odoo, tái sử dụng token và component trong asset backend hiện có; nối action tạo vị trí tới hub thật, giữ liên kết `hr.job` / `hr.applicant`. Nếu hub chạy bằng frontend riêng, chia sẻ các giá trị token và quy ước trạng thái; component OWL/Odoo cần được chuyển thể theo framework đó. Không sao chép toàn bộ SCSS native sang frontend khác và giả định sẽ chạy tương đương.

Mỗi thay đổi component phải xem cả trang tuyển dụng, ứng viên và trang phỏng vấn public có liên quan. Đổi token không làm thay đổi logic gửi thư, scheduling hay quyền dữ liệu; các luồng đó cần được kiểm tra sau tích hợp.


## Cài vào database thử

Tại thư mục repo có `docker-compose.yml`, khởi động PostgreSQL và build image:

```sh
docker compose up -d db
docker compose build odoo
```

Cài module vào database mới `hkt_ui_demo`:

```sh
docker compose run --rm --no-deps -T odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -d hkt_ui_demo -i aho_recruitment_ui --without-demo=all \
  --stop-after-init --no-http --max-cron-threads=0
```

Sau khi cài, chạy server cho đúng database:

```sh
docker compose run --rm --no-deps --service-ports odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -d hkt_ui_demo --db-filter='^hkt_ui_demo$'
```

Mở [trang tuyển dụng](http://localhost:8070/odoo/recruitment). Lệnh có `--service-ports` cần cổng 8070 chưa được một Odoo khác sử dụng. Khi một service Odoo đang chạy đúng database, chỉ cần nâng cấp module bằng `docker compose exec -T odoo odoo ... -u aho_recruitment_ui --stop-after-init --no-http`, với cùng các tham số kết nối DB phía trên, rồi tải lại trang.

Đây là lệnh cho database thuộc main mới, có `hr_recruitment`, `rag_interview_agent`, `rag_filter_agent`. Database cũ còn cài `t10_ai_interview` cần phương án migration riêng vì main đã xóa addon đó; không dùng hướng dẫn này như một migration dữ liệu cũ.

## Điểm nối với hub AI của teammate

| Thành phần | File / định danh ổn định | Cách nối |
| --- | --- | --- |
| Trang tuyển dụng | `views/recruitment.xml`, kế thừa `hr_recruitment.view_hr_job_kanban` | Hiện vẫn dùng luồng tạo vị trí mặc định `hr.action_create_job_position`. Team thay điểm mở tạo vị trí bằng hub thật khi đã có. |
| Dữ liệu vị trí | Model `hr.job` | Giữ ID vị trí xuyên suốt từ hub đến ứng viên. Tên, mô tả, recruiter, phòng ban và chỉ tiêu vẫn là trường Odoo hiện có. |
| Ứng viên theo vị trí | `hr_recruitment.action_hr_job_applications` | UI đổi tên và gắn bảng kế thừa; giữ đủ view mode, filter và `default_job_id` của core. |
| Tất cả ứng viên | `aho_recruitment_ui.action_applicant_tracking` | Ưu tiên list/card; giữ thêm graph/calendar/pivot/activity, quyền Odoo và bộ lọc core; không hardcode ID database. |
| Tóm tắt trên card | `models/applicant.py` | Hai trường tính toán `aho_interview_state`, `aho_next_action` chỉ phản ánh phiên RAG mới nhất và HR phụ trách; không phải trạng thái scheduler hay toàn bộ ATS pipeline. |
| Lịch sử AI | `views/applicant.xml`, quan hệ `rag_interview_ids` | Mở phiên/report của `rag_interview_agent`; điểm hiển thị là tham khảo, người tuyển xem bằng chứng. |
| Màu / khoảng cách | `static/src/css/design_system.css` (token theo kit Venture CRM), `workspace_components.scss` (nút, badge, card, bảng, header) | Chỉ dùng biến `--aho-*`; không khai báo lại style component ở file khác. Sidebar ở `material_sidebar.scss`. |

Quy trình tuyển dụng dùng chung theo lĩnh vực, phiên bản yêu cầu và quyền agent đọc nguồn đó chưa được module UI này triển khai. Cần thống nhất model/schema với phần backend trước khi thêm màn hình quản lý. Talent Pool metadata-only cũng chưa được bảo vệ bởi module này; việc ẩn menu không thay quyền truy cập dữ liệu.

## Card vị trí tuyển dụng

Card hiển thị tình trạng của vị trí, không liệt kê ứng viên: số ứng viên theo từng giai đoạn (bấm để mở ứng viên của giai đoạn đó), tiến độ đã tuyển trên chỉ tiêu, và cảnh báo. Cảnh báo là luật cố định trong `models/job.py` (quá hạn, sắp đến hạn mà giai đoạn cuối chưa đủ người, pipeline dưới 3 ứng viên cho mỗi chỉ tiêu còn lại, ứng viên nằm yên quá số ngày của giai đoạn hoặc 7 ngày, 14 ngày không có hồ sơ mới, phiên phỏng vấn lỗi, thiếu JD hoặc HR phụ trách). Đây không phải mô hình dự đoán. “Chưa có cảnh báo” nghĩa là chưa có luật nào kích hoạt, không phải cam kết đúng tiến độ. Số liệu theo hồ sơ người xem có quyền truy cập. Trường `aho_target_date` (Hạn tuyển) được thêm vào `hr.job`; khi tích hợp phải nâng cấp addon để tạo cột dữ liệu. Core tự giảm `no_of_recruitment` khi tuyển được người: card dùng trường này làm chỉ tiêu còn lại, tổng mục tiêu hiển thị là số đã tuyển cộng chỉ tiêu còn lại.

## Kiểm tra sau khi tích hợp

Trạng thái `ready` chỉ xác nhận đã tạo link; không dùng nhãn “Đã gửi” vì backend chưa cung cấp bằng chứng gửi thư mời. Icon dùng `.oi` + `data-icon` của Odoo 20; Font Awesome không có trong runtime đã kiểm tra.

1. Với hai vị trí và hồ sơ giả khác nhau, mở **Xem ứng viên** ở từng card: hồ sơ thuộc đúng vị trí, đổi card/list giữ bộ lọc.
2. Mở **New** từ vị trí: vị trí tuyển dụng được điền đúng; đóng nháp khi không lưu. Luồng tạo đợt tuyển qua hub cần được kiểm tra riêng khi team nối xong.
3. Kéo một card thử giữa hai giai đoạn rồi trả lại; mở profile, lịch sử AI và báo cáo vẫn được.
4. Sidebar có hai nhóm: Tuyển dụng (Vị trí, Ứng viên) và Thiết lập (Phòng ban, Quy trình tuyển dụng, Lý do từ chối, Thông tin công ty cho AI, Cài đặt; mục hiện theo quyền). Phỏng vấn tự động không có mục riêng; lịch sử và báo cáo xem trong hồ sơ ứng viên. Công cụ chấm CV độc lập vẫn thuộc addon rag_filter_agent, chưa nối kết quả vào applicant và không có lối vào trong sidebar bản này. Thu gọn còn icon 64px và mở lại được. Kiểm tra desktop và cửa sổ hẹp; không tràn trang, không mất điều hướng.
5. Bảng/search kế thừa primary từ Odoo: giữ đủ trường tùy chọn, multi-edit, ba thao tác header và bộ lọc core. Kiểm tra lại các view Calendar/Graph/Pivot/Activity; Calendar ở đây hiển thị hạn công việc, không phải bằng chứng đã có AI scheduling.
6. Kiểm thử riêng scheduling, email, voice, provider và publishing bằng luồng tích hợp thật. Kết quả UI không chứng minh các thao tác đó thành công.

## Bằng chứng hiện có và giới hạn

Đã cài/nâng cấp trên Odoo `20.0-20261015`, dùng OrbStack, PostgreSQL 16. Lượt kiểm tra 31/10/2026 đã cài vào DB mới `hkt_ui_install_check` trong image hiện tại: `aho_recruitment_ui`, `rag_interview_agent`, `rag_filter_agent` đều ở trạng thái `installed`; không cấu hình API key cho tiến trình cài thử. Bản token/component Figma đã nâng cấp thành công trên `hkt_ux_main_test`; sửa lỗi template nhóm Thiết lập, icon, cột Kanban và header khi chạy thật. Kiểm tra sáu trường hợp của Việc tiếp theo bằng record thử trong bộ nhớ, gồm stage đã tuyển và archived; không thay đổi stage hoặc gọi provider trong bài kiểm tra này. Đã kiểm tra các thao tác UI trên dữ liệu `[UX TEST]`, gồm kéo Kanban, filter, default vị trí, profile/report và viewport 390/768/1280px. Các báo cáo fixture ghi rõ là dữ liệu giả; không dùng làm kết quả AI hoặc ROI trong bài nộp.

Đã so diff với main `f444ec8`: addon UI không sửa source `hr_recruitment`, `rag_interview_agent` hoặc `rag_filter_agent`, nhưng có kế thừa view/action làm thay đổi trải nghiệm. Audit đã phát hiện và khôi phục các khả năng core từng bị thu hẹp: 34 trường list gốc, 34 bộ lọc gốc, 3 thao tác header, multi-edit và 7 view mode. Việc so source backend không thay thế kiểm tra view/action.

Main đã xóa `t10_ai_interview` và ML tại `e11da0a`; UI này không khôi phục scheduler, luật AI theo vị trí, kết quả CV gắn applicant, quyết định AI/human riêng hoặc playbook/agent log của addon cũ. Core vẫn có họp thủ công, stage/refuse/hired, ghi chú và Talent Pool. `hr.recruitment.stage` đã có `requirements`, `job_ids`, `template_id`; agent RAG hiện chưa đọc các yêu cầu giai đoạn này. Company background ở `res.company.rag_background` có thể chỉnh theo quyền company; quyền HR manager không tự tương đương quyền quản trị company/API keys.

Dockerfile của main dùng `odoo:latest`. Kiểm tra database mới trong image hiện tại không chứng minh chạy lại thành công trên một image khác hoặc máy sạch. Chốt version/runtime và commit demo trước khi ghi vào PDF cuối.

## Khi cần quay lại bản trước

Giữ bản sao database trước khi tích hợp. Nếu chỉ chỉnh giao diện, quay lại commit UI đã kiểm tra, nâng cấp `aho_recruitment_ui` và tải lại assets. Nếu đổi model/schema/backend, dùng phương án migration/khôi phục DB của team; không xóa database hoặc gỡ addon AI để xử lý lỗi giao diện.

## Kiểm tra gói bàn giao ngày 31/10/2026

- Đã fetch main `f444ec8` trước khi chốt; main là ancestor của nhánh UI.
- Đã sửa widget funnel cho OWL 3 (`useProps`, truy cập getter qua `this` trong template) và dùng bộ icon native Odoo.
- Nâng cấp trên `hkt_ux_main_test` và cài mới trên `hkt_ui_final_check` trong image Odoo hiện tại.
- Sáu kiểm tra quy tắc gồm quá hạn, counts cập nhật khi applicant thay đổi, tuyển đủ không trừ chỉ tiêu hai lần, hồ sơ nằm lâu, JD HTML trống và phiên lỗi. Các record kiểm tra được rollback, không gọi provider.
- View core giữ đủ 34 trường bảng, 34 bộ lọc, ba thao tác header và bảy chế độ xem. Bấm funnel lọc đồng thời job/stage; bảng drill-down vẫn có Việc tiếp theo. Form company context và Hạn tuyển mở được; tab phỏng vấn trống có thông báo.
- Chưa kiểm thử AI/provider, gửi thư mời, scheduling/publishing hoặc hệ thống tích hợp cuối. Bộ UI không thêm quyền đọc/sửa company hay dữ liệu ứng viên.
