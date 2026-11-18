# IssueFlow AI

Flask + SQLite + HTML/CSS/JavaScript 기반 GitHub 이슈 관리 대시보드입니다. GitHub App OAuth로 로그인하고, 접근 가능한 저장소의 이슈를 조회한 뒤 사용자가 직접 시작한 AI 분석을 확인하고 승인한 항목만 GitHub에 반영합니다.

## 로컬 실행 (Windows)

Python 3.12 이상을 권장합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

기존 `.env`가 있다면 복사하지 마세요. 기존 값을 보존하고 아래 환경변수 이름만 확인해 필요한 값을 추가합니다. 실제 API 키와 토큰은 이 파일에만 두고 절대로 커밋하지 않습니다.

| 변수 | 용도 |
| --- | --- |
| `GITHUB_APP_ID` | GitHub App 식별자 |
| `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` | 기존 GitHub OAuth 로그인 |
| `GITHUB_APP_SLUG` | GitHub App 설치 링크 |
| `FLASK_SECRET_KEY` | Flask 세션 서명 키 |
| `DATA_ENCRYPTION_KEY` | 사용자별 API 키/Webhook을 SQLite에 암호화 저장 |
| `ISSUEFLOW_DATABASE_PATH` | 선택 사항. SQLite 경로 (기본값 `instance/issueflow.sqlite3`) |
| `COOKIE_SECURE` | HTTPS 배포에서 `true`; 로컬 HTTP는 기본 `false` |

`DATA_ENCRYPTION_KEY`는 Flask 키와 별도로 생성하고 백업해야 합니다. 키를 잃으면 저장된 비밀 설정을 복호화할 수 없습니다. 로컬에서 다음 명령으로 Fernet 키를 생성해 환경변수에 직접 설정하세요. 생성된 키는 터미널에 표시되므로 공유하거나 저장소에 넣지 마세요.

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

서버 실행:

```powershell
python app.py
```

브라우저에서 `http://127.0.0.1:5000`에 접속하고 GitHub로 로그인합니다. 저장소를 선택한 뒤 OpenAI API 키와 선택적 Discord Webhook을 입력하면 서버에서 암호화해 저장합니다. 설정 화면은 비밀값을 다시 보여주지 않으며 등록 여부만 표시합니다.

## 분석 및 GitHub 반영 흐름

1. 이슈 목록에서 이슈를 열고 **AI 분석 실행**을 눌러야 분석이 시작됩니다.
2. 분석 결과는 SQLite에 사용자/저장소별로 기록됩니다. 이 단계에서는 GitHub 변경이나 Discord 알림이 없습니다.
3. 사용자가 추천 라벨 및/또는 분석 댓글을 선택하고 **선택 항목 GitHub에 적용**을 승인해야 실제 변경됩니다.
4. GitHub 반영 성공 후에만 등록된 Discord Webhook으로 알림을 보냅니다. 적용한 라벨과 관리 댓글은 재시도 시 중복을 최소화합니다.
5. 설정 화면에서 OpenAI 연결 테스트와 명시적인 Discord 테스트 메시지 전송을 실행할 수 있습니다.

저장소 생성은 사용자 확인 후 GitHub API로 실행합니다. 실제 외부 계정에 영향을 주므로 테스트에는 전용 저장소를 사용하세요.

## 데이터 및 사용자 보안

- SQLite 테이블은 서버 시작 시 `CREATE TABLE IF NOT EXISTS`로 안전하게 준비하며 기존 DB를 초기화하지 않습니다.
- 사용자 OpenAI 키와 Discord Webhook은 `DATA_ENCRYPTION_KEY`로 암호화해 저장합니다. 브라우저 요청 헤더로 전달하거나 조회 응답으로 반환하지 않습니다.
- GitHub OAuth state 검증, 서버 측 토큰 세션, 사용자 ID 기반 설정/분석 이력 조회, 같은 출처 변경 요청 검사, 보안 응답 헤더를 사용합니다.
- 로그인 토큰은 현재 서버 메모리에 보관됩니다. 서버 재시작 후에는 GitHub에 다시 로그인해야 하며 토큰을 SQLite에 보관하지 않습니다.
- SQLite에는 사용자 설정과 AI 분석 이력을 보관합니다. GitHub 원본 이슈/라벨/댓글은 GitHub에서 관리합니다.
- `.env`, `.sqlite3`, 가상환경 및 로그 파일은 `.gitignore` 대상입니다. 이미 Git 추적된 민감 파일은 별도로 `git ls-files`로 확인해야 합니다.

## GitHub Actions

Actions 워크플로는 `workflow_dispatch` 수동 실행만 지원하며 이슈 생성만으로 자동 분석하지 않습니다. 이 워크플로 실행은 읽기 전용 분석이며 웹앱의 SQLite 분석 이력에는 기록되지 않고, GitHub 변경/Discord 알림도 수행하지 않습니다. 승인 후 반영은 로그인한 웹앱을 사용하세요.

## 테스트

외부 API를 호출하지 않는 단위 테스트:

```powershell
python -m unittest discover -s tests -v
```

브라우저의 실제 GitHub/OpenAI/Discord 연결 테스트는 별도 계정과 전용 테스트 저장소에서 사용자가 직접 실행해야 합니다. 실제 이슈 생성·수정이나 Discord 테스트 메시지 전송은 자동 테스트하지 않습니다.

## 현재 범위 및 제한

현재 UI에는 이슈 생성·수정·닫기/재열기, 담당자·우선순위 설정, 댓글 CRUD, 최대 50개 이슈의 일괄 작업, 검색/상태/유형/우선순위/담당자 필터와 정렬, 라벨 기반 칸반 보기가 포함됩니다. 새 저장소 생성 시 README, 기본 라벨과 기본 이슈 템플릿을 선택할 수 있으며 기존 항목은 덮어쓰지 않습니다.

기본 DB는 로컬 SQLite를 사용하므로 휘발성 파일시스템이나 다중 인스턴스 배포에는 적합하지 않습니다. 이번 작업은 배포 전환을 다루지 않습니다. 추후 배포 시에는 영속 디스크/백업, HTTPS와 secure 쿠키, `FLASK_SECRET_KEY` 및 `DATA_ENCRYPTION_KEY` 안전한 보관, OAuth callback URL, GitHub App 최소 권한, 로그/CSRF/요청 제한 점검이 필요합니다.

## 파일 구조

```text
app.py                         Flask 라우트, 인증, GitHub App 흐름
agent/config.py                환경변수 및 서비스 설정
agent/storage.py               SQLite 사용자 설정/분석 이력 및 암호화
agent/analyzer.py              구조화된 GPT 이슈 분석
agent/github_client.py         GitHub Issues API
agent/service.py               분석과 승인 반영 흐름
agent/notifier.py              승인 후 Discord 알림
templates/index.html           기존 대시보드 템플릿
public/app.js                  프론트엔드 동작
public/styles.css              기존 디자인 시스템
tests/                         외부 API 없는 테스트
```
