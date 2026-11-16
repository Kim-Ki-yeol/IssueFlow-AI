# IssueFlow AI

GitHub 이슈가 등록되면 GPT가 유형과 우선순위를 분석하고, 라벨과 분석 댓글을 등록하는 프로젝트입니다. Discord 알림도 보낼 수 있습니다.

- 프론트엔드: HTML / CSS / JavaScript
- 백엔드: Python / Flask
- AI 분석: OpenAI GPT API / LangChain
- 자동 실행: GitHub Actions
- 연동: GitHub REST API / Discord Webhook

**실제 GitHub 전용 버전입니다.** 샘플 이슈, 규칙 기반 분석, SQLite, 데모 API는 들어 있지 않습니다. 이슈와 분석 기록은 GitHub가 관리합니다. 이 버전은 본인 저장소를 관리하는 **관리자용 홈페이지**이며, 누구나 공개적으로 분석 버튼을 누를 수 있게 만든 서비스는 아닙니다.

## 1. 처음 실행하기 (Windows)

Python 3.12 이상을 추천합니다. VS Code 터미널에서 아래 순서로 실행하면 됩니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

이미 `.env` 파일이 있다면 복사 명령은 생략하세요. `.env`를 열어서 다음 내용을 설정합니다.

```ini
GITHUB_REPO=본인계정/테스트저장소
GITHUB_TOKEN=발급받은_GitHub토큰
OPENAI_API_KEY=발급받은_OpenAI_API키
OPENAI_MODEL=gpt-4.1-mini
ENABLE_GITHUB_WRITES=false
DASHBOARD_PASSWORD=본인이_정한_긴_비밀번호
DISCORD_WEBHOOK_URL=
```

**키를 Python 코드의 `os.getenv("KEY", "")` 안에 넣는 게 아닙니다.** `.env`의 등호 뒤에 입력하세요. `.env`는 GitHub에 올리면 안 됩니다.

```powershell
python app.py
```

브라우저에서 `http://127.0.0.1:5000`을 열면 됩니다. 기본 인증 창이 나오면 **아이디 `admin`**, 비밀번호는 `DASHBOARD_PASSWORD` 값을 입력하세요.

`RUN_WINDOWS.bat`을 실행해도 가상환경 설치와 서버 시작을 할 수 있습니다. 단, `.env` 설정은 직접 해야 합니다.

## 2. 토큰 발급하기

### GitHub 토큰

1. GitHub에 테스트용 저장소를 하나 만듭니다. Issues 기능을 켜두세요.
2. <https://github.com/settings/personal-access-tokens/new>에서 Fine-grained token을 만듭니다.
3. **Only select repositories**로 테스트 저장소만 선택합니다.
4. Repository permissions: **Issues = Read and write**, **Contents = Read**로 설정합니다.
5. 생성된 토큰을 로컬 `.env`의 `GITHUB_TOKEN`에 넣습니다.

토큰은 페이지에서 다시 보여주지 않으므로 따로 안전하게 보관하세요.

### OpenAI API 키

<https://platform.openai.com/api-keys>에서 프로젝트 API 키를 생성하고 `OPENAI_API_KEY`에 넣습니다. **ChatGPT 구독과 API 사용료는 별도**입니다.

### Discord Webhook (선택)

Discord 서버 설정 → **연동 / Integrations → Webhooks → New Webhook**에서 채널을 선택하고 URL을 복사합니다. `DISCORD_WEBHOOK_URL`에 넣으면 실제 이슈 분석이 GitHub에 반영된 뒤 알림이 갑니다. URL도 키처럼 비공개로 관리하세요.

## 3. 실제 연결 테스트

먼저 `ENABLE_GITHUB_WRITES=false`로 시작하는 걸 추천합니다. 이 설정에서도 **실제 GPT API를 호출하므로 요금은 발생**하지만 GitHub 라벨과 댓글은 수정하지 않습니다.

1. GitHub 테스트 저장소의 Issues에 실제 이슈를 하나 만듭니다.
2. 로컬 홈페이지 새로고침 후 이슈가 보이는지 확인합니다.
3. 해당 이슈를 선택해 **AI 분석 실행**을 누릅니다.
4. 분석 결과가 표시되는지 확인합니다. 이 단계는 읽기 전용이며 재접속하면 결과가 남지 않습니다.
5. `.env`에서 `ENABLE_GITHUB_WRITES=true`로 변경한 다음 Flask 서버를 다시 실행합니다.
6. **AI 분석 실행**을 다시 누릅니다. GitHub 이슈에 라벨과 GPT 분석 댓글이 생기면 성공입니다.
7. Discord URL이 설정되어 있다면 분석 결과 알림이 도착하는지 확인합니다.
8. 이미 분석된 이슈는 건너뛰며, **다시 분석**을 선택하면 기존 분석 댓글을 업데이트합니다.

이슈가 많아도 이 버전은 **최근 수정된 열린 이슈 최대 60개**만 표시합니다. 최근 활동도 GitHub 이슈의 업데이트 시각을 보여주는 것이며 별도 감사 로그는 아닙니다.

## 4. GitHub Actions 자동 실행

`.github/workflows/issue-agent.yml`이 **이슈가 발생하는 바로 그 저장소의 기본 브랜치**에 있어야 합니다.

1. 이 프로젝트의 파일을 GitHub 테스트 저장소에 올립니다. `.env`와 `.venv`는 올리지 않습니다.
2. `Settings → Secrets and variables → Actions → New repository secret`에 `OPENAI_API_KEY`를 등록합니다.
3. 알림도 사용한다면 `DISCORD_WEBHOOK_URL`을 등록합니다.
4. `Settings → Actions → General`에서 Actions가 사용 가능한지 확인합니다. 조직/저장소 정책에 따라 권한 설정이 필요할 수 있습니다.
5. 기존 이슈를 수정하지 말고 **새 이슈**를 하나 등록합니다.
6. `Actions`에서 `IssueFlow AI - Issue Triage` 실행 결과를 확인합니다.
7. GitHub 이슈에 `bug`, `priority:high`, `ai:processed` 같은 라벨과 분석 댓글이 생기면 성공입니다. 실제 분류는 이슈 내용에 따라 달라집니다.

Actions에서 사용하는 `GITHUB_TOKEN`은 GitHub가 자동으로 발급하므로 개인 PAT를 별도로 Secret에 등록할 필요는 없습니다. 워크플로우에 `issues: write` 권한을 지정해 두었습니다.

**중요:** 홈페이지(Flask/Vercel)와 Actions는 서로 독립적으로 실행됩니다. 분석 결과는 둘 다 GitHub 이슈에 저장하므로, 홈페이지에서 다시 조회할 수 있습니다. GitHub Actions만 사용할 때는 `DASHBOARD_PASSWORD`를 설정하지 않아도 됩니다.

## 5. Vercel 배포

Flask `app.py`를 Vercel에서 실행하도록 구성했고, CSS/JS는 `public/`으로 이동했습니다. 별도 SQLite는 필요하지 않습니다.

1. 프로젝트 코드를 GitHub에 올립니다. `.env`는 절대 올리지 않습니다.
2. <https://vercel.com/new>에서 해당 저장소를 Import합니다.
3. **Root Directory**를 `app.py`가 있는 폴더로 선택합니다. 이 ZIP은 `IssueFlow-AI/` 폴더가 최상위입니다.
4. 프레임워크는 Flask 자동 감지를 사용하고, Build Command는 따로 설정하지 않습니다.
5. `Settings → Environment Variables`에 아래 값을 등록합니다.

| 변수 | 값 |
| --- | --- |
| `GITHUB_REPO` | `본인계정/테스트저장소` |
| `GITHUB_TOKEN` | Fine-grained PAT |
| `OPENAI_API_KEY` | OpenAI API 키 |
| `OPENAI_MODEL` | `gpt-4.1-mini` |
| `ENABLE_GITHUB_WRITES` | 처음 `false`, 점검 후 `true` |
| `DASHBOARD_PASSWORD` | 긴 랜덤 관리자 비밀번호 |
| `DISCORD_WEBHOOK_URL` | 선택 사항 |

환경변수는 서버에서만 사용합니다. 환경변수 변경 후엔 **Redeploy**해야 적용됩니다. Vercel의 Preview 배포에도 같은 키를 무분별하게 넣지 않는 게 좋습니다.

Vercel 배포에서 주의할 점:

- Basic Auth를 사용하므로 **관리자 전용**입니다. 다른 사람이 GitHub 데이터를 자유롭게 분석하도록 열어주려면 GitHub OAuth, 사용자별 저장소 권한 확인, API 호출 제한 등을 추가해야 합니다.
- Vercel에서 실행 시간이 길어질 수 있으므로 실제 GPT API 응답 시간을 점검하세요.
- 토큰과 Webhook URL은 브라우저의 JavaScript에 절대 넣지 않습니다.
- 사이트에 접속할 때 인증을 요구하는 것이 정상입니다.

## 6. 파일 구조

```text
IssueFlow-AI/
├── app.py                         # Flask 홈페이지와 API
├── agent/
│   ├── config.py                  # .env / 서버 환경변수
│   ├── analyzer.py                # 실제 GPT 이슈 분석
│   ├── github_client.py           # GitHub 조회, 라벨 및 댓글 등록
│   ├── notifier.py                # Discord Webhook
│   ├── service.py                 # 전체 처리 흐름
│   └── workflow.py                # GitHub Actions 실행 파일
├── templates/index.html           # 홈페이지
├── public/app.js                  # 화면 동작
├── public/styles.css              # 화면 디자인
├── .github/workflows/issue-agent.yml
├── .python-version                # Vercel Python 버전
├── .vercelignore                  # 배포 제외 파일
├── .env.example                   # 환경변수 예시 (실제 키 없음)
├── RUN_WINDOWS.bat                # Windows 실행
├── requirements.txt
├── tests/                         # API를 호출하지 않는 단위 테스트
└── README.md
```

## 7. 테스트 명령어

```powershell
python -m unittest discover -s tests -v
```

단위 테스트는 GitHub/OpenAI/Discord를 실제로 호출하지 않습니다. 실제 연동은 3~4번 단계를 따로 확인해야 합니다.

## 8. 보안 및 한계

- GitHub 라벨·댓글 외에 소스 수정, PR 생성, 임의 명령 실행은 하지 않습니다.
- GPT는 이슈 내용을 기반으로 추정할 뿐 실제 장애 원인을 확정하지 않습니다.
- 이슈 본문의 지시문은 AI의 시스템 지시로 취급하지 않습니다.
- Basic Auth, GitHub 저장소 제한, 사용자별 Secret으로 **본인만 관리하는 용도**입니다.
- 공개 저장소에서는 누구나 새 이슈를 생성해 Actions의 GPT 비용을 발생시킬 수 있습니다. 실서비스로 운영할 때는 승인 조건이나 호출 제한을 추가하세요.
- 데이터베이스나 감사 로그 기능은 없습니다. 최근 업데이트는 GitHub 이슈 데이터를 표시합니다.
- 배포·외부 API 호출은 계정과 키가 필요하므로 각각의 환경에서 테스트해야 합니다.

## 공식 문서

- [GitHub REST Issues](https://docs.github.com/en/rest/issues)
- [GitHub Actions 이벤트](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issues)
- [Vercel Flask 배포](https://vercel.com/docs/frameworks/backend/flask)
- [Discord Webhooks](https://discord.com/developers/docs/resources/webhook)
