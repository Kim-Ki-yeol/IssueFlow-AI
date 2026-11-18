
/* IssueFlow AI - GitHub App 기반 대시보드 */

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const state = {
  config: null, issues: [], activity: [], selected: null,
  busy: false, view: 'dashboard', repositories: [],
  selectedRepo: '', analysisHistoryId: null,
  connected: false
};

let connectionId = 0;
let loggingOut = false;
let authCheckId = 0;

const icons = {
  check: 'i-check', spark: 'i-spark', circle: 'i-circle',
  github: 'i-github', bell: 'i-bell'
};

function icon(name, cls = 'icon') {
  return `<svg class="${cls}" aria-hidden="true"><use href="#${icons[name] || name}"/></svg>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;',
    '"': '&quot;', "'": '&#39;'
  }[c]));
}

function safeGithubUrl(value) {
  const url = String(value || '');
  return /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/(issues|pull)\/\d+$/.test(url)
    ? url : null;
}

function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit'
  }).format(date);
}

function miniDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'short', day: 'numeric'
  }).format(date);
}

function category(issue) {
  if (issue.analysis?.category) return issue.analysis.category;
  const labels = issue.labels || [];
  if (labels.includes('bug')) return 'bug';
  if (labels.includes('enhancement')) return 'feature';
  if (labels.includes('documentation')) return 'documentation';
  if (labels.includes('question')) return 'question';
  return 'unclassified';
}

function priority(issue) {
  if (issue.analysis?.priority) return issue.analysis.priority;
  const label = (issue.labels || []).find(v => v.startsWith('priority:'));
  const value = label ? label.split(':')[1] : 'none';
  return ['high', 'medium', 'low'].includes(value) ? value : 'none';
}

function processed(issue) {
  return (issue.labels || []).includes('ai:processed') || Boolean(issue.analysis);
}

const categoryLabel = {
  bug: 'Bug', feature: 'Feature', documentation: 'Docs',
  question: 'Question', unclassified: '미분류'
};

const priorityLabel = {
  high: '높음', medium: '보통', low: '낮음', none: '미정'
};

// Flask API 호출
async function api(path, options = {}) {
  const controller = new AbortController();
  const { timeoutMs = 30000, ...requestOptions } = options;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const headers = { ...(requestOptions.headers || {}) };

    if (requestOptions.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(path, {
      ...requestOptions, headers, signal: controller.signal,
      cache: 'no-store', credentials: 'same-origin'
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('요청 시간이 초과되었습니다. 다시 시도해 주세요.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function toast(message, type = 'success') {
  const parent = $('#toastContainer');
  if (!parent) return;
  const item = document.createElement('div');
  item.className = `toast ${type}`;
  item.textContent = message;
  parent.appendChild(item);
  setTimeout(() => item.remove(), 4500);
}

// 초기 연결 화면
function showSetupStep(step) {
  ['setupLogin', 'setupRepository', 'setupCredentials'].forEach(id => {
    const element = $(`#${id}`);
    if (element) element.hidden = id !== step;
  });
  $('#setupScreen').hidden = false;
}

function showRepositoryTab(type) {
  const selecting = type === 'select';
  $('#selectRepoPanel').hidden = !selecting;
  $('#createRepoPanel').hidden = selecting;
  $('#selectRepoTab').classList.toggle('active', selecting);
  $('#createRepoTab').classList.toggle('active', !selecting);
  $('#repoSetupError').textContent = '';
}

function clearGithubUser() {
  const username = $('#setupUsername');
  const avatar = $('#setupAvatar');
  const repoName = $('#selectedRepoName');
  if (username) username.textContent = '';
  if (avatar) {
    avatar.removeAttribute('src');
    avatar.hidden = true;
  }
  if (repoName) repoName.textContent = '';
}

// 로그아웃 후 화면과 데이터 초기화
function resetWorkspace() {
  connectionId++;
  authCheckId++;

  Object.assign(state, {
    config: null, issues: [], activity: [], selected: null,
    busy: false, view: 'dashboard', repositories: [],
    selectedRepo: '', analysisHistoryId: null,
    connected: false
  });

  clearGithubUser();
  $('#setupForm').reset();
  $('#setupError').textContent = '';
  $('#repoSetupError').textContent = '';
  $('#setupRepoSearch').value = '';
  $('#issueSearch').value = '';
  $('#issueFilter').value = 'all';

  closeDrawer();
  renderIssues();
  renderActivity();

  $('#integrationRows').replaceChildren();
  $('#sidebarMode').textContent = 'GitHub 연결 대기';
  $('#topMode').textContent = '연결 대기';

  showSetupStep('setupLogin');
  selectView('dashboard');
}

// 로그인 상태 확인
async function checkLogin() {
  if (loggingOut) return;

  const id = ++authCheckId;
  const connection = connectionId;
  const result = await api('/api/auth/me');

  // 오래된 인증 확인 결과는 화면에 적용하지 않음
  if (loggingOut || id !== authCheckId || connection !== connectionId) return;

  if (!result.authenticated) {
    state.selectedRepo = '';
    state.repositories = [];
    clearGithubUser();
    showSetupStep('setupLogin');
    return;
  }

  const user = result.user || {};
  $('#setupUsername').textContent = user.login || 'GitHub 사용자';

  if (user.avatar_url) {
    $('#setupAvatar').src = user.avatar_url;
    $('#setupAvatar').hidden = false;
  } else {
    $('#setupAvatar').removeAttribute('src');
    $('#setupAvatar').hidden = true;
  }

  if (result.selected_repo) {
    state.selectedRepo = result.selected_repo;
    $('#selectedRepoName').textContent = result.selected_repo;
    const settings = await api('/api/settings');
    if (settings.openai_configured) {
      await activateWorkspace();
    } else {
      showSetupStep('setupCredentials');
    }
    return;
  }

  state.selectedRepo = '';
  $('#selectedRepoName').textContent = '';
  showSetupStep('setupRepository');
  await loadRepositories();
}

// GitHub App 설치된 저장소 조회
async function loadRepositories() {
  if (loggingOut) return;

  const list = $('#setupRepoList');
  const id = connectionId;
  list.textContent = '저장소 목록을 불러오는 중입니다.';

  try {
    const installations = await api('/api/github/installations');
    const all = [];

    for (const installation of installations.items || []) {
      if (loggingOut || id !== connectionId) return;

      const result = await api(
        `/api/github/installations/${installation.id}/repositories`
      );

      for (const repo of result.items || []) {
        all.push({ ...repo, installation_id: installation.id });
      }
    }

    if (loggingOut || id !== connectionId) return;
    state.repositories = all;
    renderRepositoryList();
  } catch (error) {
    if (loggingOut || id !== connectionId) return;
    list.textContent = '저장소를 불러오지 못했습니다.';
    $('#repoSetupError').textContent = error.message;
  }
}

function renderRepositoryList() {
  const list = $('#setupRepoList');
  const search = $('#setupRepoSearch').value.trim().toLowerCase();
  const repositories = state.repositories.filter(repo =>
    repo.full_name.toLowerCase().includes(search)
  );

  list.replaceChildren();

  if (!repositories.length) {
    const empty = document.createElement('p');
    empty.className = 'setup-hint';
    empty.textContent = state.repositories.length
      ? '검색 결과가 없습니다.'
      : '연결 가능한 저장소가 없습니다. GitHub App 설치를 확인해 주세요.';
    list.appendChild(empty);
    return;
  }

  for (const repo of repositories) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'setup-repo-item';

    const content = document.createElement('span');
    const name = document.createElement('strong');
    const description = document.createElement('small');

    name.textContent = repo.full_name;
    description.textContent = repo.private ? 'Private 저장소' : 'Public 저장소';

    content.append(name, description);
    button.appendChild(content);
    button.addEventListener('click', () => selectRepository(repo));
    list.appendChild(button);
  }
}

async function selectRepository(repo) {
  if (loggingOut) return;

  const error = $('#repoSetupError');
  const id = connectionId;
  error.textContent = '';

  try {
    await api('/api/github/select-repository', {
      method: 'POST',
      body: JSON.stringify({
        repo: repo.full_name,
        installation_id: repo.installation_id
      })
    });

    if (loggingOut || id !== connectionId) return;

    state.selectedRepo = repo.full_name;
    $('#selectedRepoName').textContent = repo.full_name;
    showSetupStep('setupCredentials');
  } catch (err) {
    if (!loggingOut && id === connectionId) error.textContent = err.message;
  }
}

// 새 GitHub 저장소 생성
async function createRepository(event) {
  event.preventDefault();
  if (loggingOut) return;

  const button = $('#createRepoSubmit');
  const error = $('#repoSetupError');
  const name = $('#newRepoName').value.trim();
  const description = $('#newRepoDescription').value.trim();
  const isPrivate = $('#newRepoVisibility').value === 'private';
  const id = connectionId;

  error.textContent = '';

  if (!/^[A-Za-z0-9_.-]{1,100}$/.test(name)) {
    error.textContent = '저장소 이름을 확인해 주세요.';
    return;
  }

  const visibility = isPrivate ? '비공개' : '공개';

  if (!window.confirm(
    `${name} 저장소를 ${visibility}로 생성할까요?\n실제 GitHub 계정에 저장소가 생성됩니다.`
  )) return;

  button.disabled = true;
  button.textContent = '저장소 생성 중...';

  try {
    const result = await api('/api/github/repositories', {
      method: 'POST',
      body: JSON.stringify({ name, description, private: isPrivate }),
      timeoutMs: 45000
    });

    if (loggingOut || id !== connectionId) return;

    toast(`${result.repo} 저장소가 생성되었습니다.`);
    showRepositoryTab('select');
    await loadRepositories();

    if (loggingOut || id !== connectionId) return;

    const created = state.repositories.find(repo => repo.full_name === result.repo);

    if (created) {
      await selectRepository(created);
    } else {
      error.textContent =
        '저장소 생성은 완료되었습니다. GitHub App 설치 설정에서 새 저장소의 접근을 허용한 뒤 목록을 다시 불러와 주세요.';

      if (result.html_url) {
        const link = document.createElement('a');
        link.href = result.html_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = '생성한 저장소 보기';
        error.appendChild(document.createElement('br'));
        error.appendChild(link);
      }
    }
  } catch (err) {
    if (!loggingOut && id === connectionId) error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = '새 저장소 생성하기';
  }
}

// OpenAI 연결
async function connectWorkspace(event) {
  event.preventDefault();
  if (loggingOut) return;

  const button = $('#setupSubmit');
  const error = $('#setupError');
  const key = $('#setupOpenaiKey').value.trim();
  const discord = $('#setupDiscord').value.trim();
  const id = connectionId;

  error.textContent = '';

  button.disabled = true;
  button.textContent = '설정 저장 중...';

  try {
    const saved = await api('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({ openai_api_key: key, discord_webhook_url: discord })
    });

    if (loggingOut || id !== connectionId) return;
    if (!saved.openai_configured) {
      throw new Error('OpenAI API Key를 입력해 주세요.');
    }
    await activateWorkspace();
    if (!loggingOut && id === connectionId) toast(`${state.config.repo} 저장소에 연결되었습니다.`);
  } catch (err) {
    if (!loggingOut && id === connectionId) error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = '워크스페이스 시작하기';
  }
}

async function activateWorkspace() {
  const [config, issues, activity, analysis] = await Promise.all([
    api('/api/config'), api('/api/issues'), api('/api/activity'), api('/api/analysis-history')
  ]);
  state.config = config;
  state.issues = issues.items || [];
  state.activity = activity.items || [];
  state.connected = true;
  state.selectedRepo = config.repo;

  renderConfig();
  renderIssues();
  renderActivity();
  renderAnalysisStats(analysis.statistics);

  $('#setupForm').reset();
  $('#setupScreen').hidden = true;
  selectView('dashboard');
  loadSettingStatus();
}

async function saveIntegrationSettings(event) {
  event.preventDefault();
  try {
    const result = await api('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({
        openai_api_key: $('#settingsOpenaiKey').value.trim(),
        discord_webhook_url: $('#settingsDiscord').value.trim()
      })
    });
    $('#settingsOpenaiKey').value = '';
    $('#settingsDiscord').value = '';
    await loadSettingStatus();
    await refresh(true);
    toast(result.openai_configured ? '연동 설정을 저장했습니다.' : 'OpenAI API Key를 등록해 주세요.');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function loadSettingStatus() {
  try {
    const result = await api('/api/settings');
    $('#openaiKeyStatus').textContent = result.openai_configured ? '설정됨 · 원본 비공개' : '미설정';
    $('#discordKeyStatus').textContent = result.discord_configured ? '설정됨 · 원본 비공개' : '미설정';
  } catch (_) {
    // Do not surface key or server details in the page.
  }
}

async function testIntegration(path) {
  try {
    const result = await api(path, { method: 'POST', body: JSON.stringify({}) });
    toast(result.message || '연결을 확인했습니다.');
  } catch (error) {
    toast(error.message, 'error');
  }
}

// GitHub 로그아웃
async function disconnectWorkspace() {
  if (loggingOut) return;
  if (!window.confirm('IssueFlow AI에서 로그아웃할까요?')) return;

  loggingOut = true;
  connectionId++;
  authCheckId++;

  const button = $('#disconnectBtn');
  if (button) button.disabled = true;

  try {
    await api('/api/auth/logout', {
      method: 'POST',
      body: JSON.stringify({})
    });

    const auth = await api('/api/auth/me');

    if (auth.authenticated) {
      throw new Error('로그아웃 후에도 로그인 상태입니다. 서버 세션을 확인해 주세요.');
    }

    resetWorkspace();
    toast('IssueFlow AI에서 로그아웃되었습니다.');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    loggingOut = false;
    if (button) button.disabled = false;
  }
}

// 대시보드 화면 이동
function selectView(name) {
  const views = {
    dashboard: '대시보드', issues: '이슈 관리',
    workflow: '자동화 워크플로우', settings: '연동 설정'
  };
  if (!(name in views)) return;

  state.view = name;

  $$('.view').forEach(view => {
    view.classList.toggle(
      'active',
      view.id === `view${name.charAt(0).toUpperCase() + name.slice(1)}`
    );
  });

  $$('.nav-link').forEach(link => {
    link.classList.toggle('active', link.dataset.nav === name);
  });

  $('#breadcrumbTitle').textContent = views[name];
  $('#sidebar').classList.remove('mobile-open');

  history.replaceState(null, '', `#${name}`);
  window.scrollTo({ top: 0, behavior: 'instant' });
}

// 이슈 목록
function issueRow(issue, full = false) {
  const name = escapeHtml(issue.title);
  const number = Number(issue.number);
  const type = category(issue);
  const level = priority(issue);
  const done = processed(issue);

  return `
    <tr class="issue-row" data-issue="${number}" tabindex="0">
      <td>
        <span class="issue-name" title="${name}">${name}</span>
        <span class="issue-id">#${number} · ${escapeHtml(issue.repository || '')}</span>
      </td>
      <td><span class="badge ${type}">${categoryLabel[type] || '미분류'}</span></td>
      <td><span class="badge ${level}">${priorityLabel[level] || '미정'}</span></td>
      <td><span class="status ${done ? 'done' : 'pending'}">${done ? '분석 완료' : '대기 중'}</span></td>
      ${full ? `<td>${escapeHtml(miniDate(issue.created_at))}</td>` : ''}
      <td>${icon('i-arrow', 'icon row-chevron')}</td>
    </tr>
  `;
}

function renderMetrics() {
  const total = state.issues.length;
  const done = state.issues.filter(processed).length;
  const high = state.issues.filter(issue => priority(issue) === 'high').length;

  $('#metricTotal').textContent = total;
  $('#metricDone').textContent = done;
  $('#metricHigh').textContent = high;
  $('#metricPending').textContent = total - done;
  $('#metricDonePct').textContent =
    `${total ? Math.round(done / total * 100) : 0}% 완료`;

  $('#navCount').textContent = total;
  $('#issueCount').textContent = total;
}

function renderAnalysisStats(stats = {}) {
  $('#metricAiTotal').textContent = stats.total || 0;
  $('#metricAiApplied').textContent = stats.applied || 0;
  $('#metricAiPending').textContent = stats.pending || 0;
  $('#metricAiFailed').textContent = stats.failed || 0;
}

function renderIssues() {
  renderMetrics();

  const recent = state.issues.slice(0, 5);
  $('#recentIssues').innerHTML = recent.length
    ? recent.map(issue => issueRow(issue)).join('')
    : '<tr><td colspan="5" class="empty-cell">표시할 이슈가 없습니다.</td></tr>';

  const query = $('#issueSearch').value.trim().toLowerCase();
  const filter = $('#issueFilter').value;

  const visible = state.issues.filter(issue => {
    const matchesSearch = !query ||
      String(issue.title || '').toLowerCase().includes(query) ||
      String(issue.number).includes(query);

    const matchesFilter = filter === 'all' ||
      (filter === 'pending' && !processed(issue)) ||
      (filter === 'processed' && processed(issue)) ||
      (filter === 'high' && priority(issue) === 'high');

    return matchesSearch && matchesFilter;
  });

  $('#allIssues').innerHTML = visible.length
    ? visible.map(issue => issueRow(issue, true)).join('')
    : '<tr><td colspan="6" class="empty-cell">검색 조건에 맞는 이슈가 없습니다.</td></tr>';

  $('#listSummary').textContent =
    `총 ${state.issues.length}개 중 ${visible.length}개 표시`;

  $$('.issue-row').forEach(row => {
    const number = Number(row.dataset.issue);
    row.addEventListener('click', () => openDrawer(number));
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter') openDrawer(number);
    });
  });
}

// 최근 활동
function renderActivity() {
  const activities = state.activity;

  $('#activityList').innerHTML = activities.length
    ? activities.map(activity => `
      <div class="activity-item">
        <span class="activity-symbol">
          ${icon(activity.kind === 'created' ? 'circle' : 'check')}
        </span>
        <div>
          <strong>${escapeHtml(activity.title)}</strong>
          <p>#${Number(activity.issue_number)} · ${escapeHtml(activity.detail)}</p>
          <small>${escapeHtml(formatDate(activity.created_at))}</small>
        </div>
      </div>
    `).join('')
    : '<p class="subtle-empty">아직 처리 기록이 없습니다.</p>';
}

// 연동 설정
function integration(iconName, name, description, enabled, status) {
  return `
    <div class="integration-row">
      <div class="integration-main">
        <div class="integration-icon">${icon(iconName)}</div>
        <div>
          <strong>${escapeHtml(name)}</strong>
          <p>${escapeHtml(description)}</p>
        </div>
      </div>
      <span class="integration-state ${enabled ? 'enabled' : ''}">
        ${escapeHtml(status)}
      </span>
    </div>
  `;
}

function renderConfig() {
  const config = state.config;
  if (!config) return;

  $('#sidebarMode').textContent = config.repo || 'GitHub 연결됨';
  $('#topMode').textContent = 'GitHub workspace';

  $('#integrationRows').innerHTML = [
    integration('github', 'GitHub REST API',
      config.repo || '저장소 미설정', config.github_enabled,
      config.github_enabled ? '설정됨' : '설정 필요'),
    integration('spark', 'OpenAI GPT API',
      `모델: ${config.model}`, config.ai_enabled,
      config.ai_enabled ? '설정됨' : '설정 필요'),
    integration('bell', 'Discord Webhook',
      '이슈 분석 알림 (선택)', config.discord_enabled,
      config.discord_enabled ? '설정됨' : '미설정'),
    integration('check', 'GitHub 쓰기 권한',
      '라벨 및 분석 댓글 등록', config.writes_enabled,
      config.writes_enabled ? '활성화' : '읽기 전용')
  ].join('');
}

// 최신 데이터 조회
async function refresh(silent = false) {
  if (!state.connected || loggingOut) return;
  const id = connectionId;

  try {
    const [config, issues, activity, analysis] = await Promise.all([
      api('/api/config'), api('/api/issues'), api('/api/activity'),
      api('/api/analysis-history')
    ]);

    if (loggingOut || id !== connectionId) return;

    state.config = config;
    state.issues = issues.items || [];
    state.activity = activity.items || [];
    renderAnalysisStats(analysis.statistics);

    renderConfig();
    renderIssues();
    renderActivity();

    if (!silent) toast('최신 정보를 불러왔습니다.');
  } catch (err) {
    if (!loggingOut && id === connectionId) toast(err.message, 'error');
  }
}

// GitHub에서 새 이슈 작성
function openGithubNewIssue() {
  const repo = state.config?.repo || state.selectedRepo;

  if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) {
    window.open(
      `https://github.com/${repo}/issues/new`,
      '_blank', 'noopener,noreferrer'
    );
  } else {
    toast('GitHub 저장소를 먼저 연결해 주세요.', 'error');
  }
}

// 이슈 상세 패널
async function openDrawer(number) {
  if (!state.connected || loggingOut) return;
  const id = connectionId;

  $('#drawerOverlay').hidden = false;
  $('#detailDrawer').classList.add('open');
  $('#detailDrawer').setAttribute('aria-hidden', 'false');

  state.selected = number;
  $('#drawerContent').innerHTML =
    '<p class="subtle-empty">이슈 상세를 불러오는 중입니다.</p>';

  try {
    const issue = await api(`/api/issues/${number}`);
    if (!loggingOut && id === connectionId && state.selected === number) {
      drawDetail(issue);
    }
  } catch (err) {
    if (!loggingOut && id === connectionId) {
      toast(err.message, 'error');
      $('#drawerContent').innerHTML =
        '<p class="subtle-empty">이슈 상세 정보를 가져올 수 없습니다.</p>';
    }
  }
}

function closeDrawer() {
  state.selected = null;
  $('#drawerOverlay').hidden = true;
  $('#detailDrawer').classList.remove('open');
  $('#detailDrawer').setAttribute('aria-hidden', 'true');
}

function drawDetail(issue) {
  const done = processed(issue);
  const type = category(issue);
  const level = priority(issue);
  const analysis = issue.analysis;
  state.analysisHistoryId = issue.analysis_id || state.analysisHistoryId;

  let analysisHtml = `
    <div class="analysis-placeholder">
      ${icon('spark')}
      아직 분석 결과가 없습니다.
      아래의 AI 분석 실행 버튼을 눌러 확인하세요.
    </div>
  `;

  if (analysis) {
    const suggestions = Array.isArray(analysis.suggestions)
      ? analysis.suggestions : [];

    const discordRetry = issue.discord_status === 'failed'
      ? '<button type="button" class="btn btn-outline btn-sm" id="retryDiscordBtn">Discord 알림 재시도</button>'
      : '';
    analysisHtml = `
      <div class="analysis-card">
        <div class="analysis-header">
          <strong>AI 분석 리포트</strong>
          <span class="badge ${level}">${priorityLabel[level]}</span>
        </div>
        <p class="analysis-note">
          GPT 분석 · 진단 결과는 추정이며 실제 원인은 확인이 필요합니다.
        </p>
        <div class="analysis-meta">
          <div>
            <small>이슈 유형</small>
            <strong>${categoryLabel[type] || '기타'}</strong>
          </div>
          <div>
            <small>관련 영역</small>
            <strong>${escapeHtml(analysis.area || '-')}</strong>
          </div>
        </div>
        <div class="analysis-detail">
          <h4>핵심 요약</h4>
          <p>${escapeHtml(analysis.summary || '-')}</p>
        </div>
        <div class="analysis-detail">
          <h4>추정 원인</h4>
          <p>${escapeHtml(analysis.probable_cause || '-')}</p>
        </div>
        <div class="analysis-detail">
          <h4>권장 점검 방법</h4>
          <ul>${suggestions.map(v => `<li>${escapeHtml(v)}</li>`).join('')}</ul>
        </div>
        <div class="analysis-detail">
          <h4>해결 방향</h4>
          <p>${escapeHtml(analysis.solution || suggestions.join('\n') || '-')}</p>
        </div>
        <div class="analysis-detail">
          <h4>GitHub 댓글 미리보기</h4>
          <div class="detail-body">${escapeHtml(analysis.comment_preview || analysis.summary || '-')}</div>
        </div>
        ${issue.analysis_comparison && Object.keys(issue.analysis_comparison).length
          ? `<div class="analysis-detail"><h4>이전 분석과 달라진 항목</h4><ul>${
            Object.entries(issue.analysis_comparison).map(([key, change]) =>
              `<li>${escapeHtml(key)}: ${escapeHtml(String(change.previous))} → ${escapeHtml(String(change.current))}</li>`
            ).join('')
          }</ul></div>` : ''}
        <div class="analysis-detail">
          <h4>GitHub 반영 미리보기</h4>
          <label><input type="checkbox" id="applyRecommendedLabels" checked> 추천 라벨 적용: ${escapeHtml((analysis.recommended_labels || []).join(', '))}</label><br>
          <label><input type="checkbox" id="applyAnalysisComment" checked> AI 분석 댓글 등록</label>
          <button type="button" class="btn btn-outline btn-sm" id="applyAnalysisBtn">선택 항목 GitHub에 적용</button>
          ${discordRetry}
        </div>
      </div>
    `;
  }

  $('#drawerContent').innerHTML = `
    <div class="detail-id">
      ISSUE #${Number(issue.number)} · ${escapeHtml(issue.repository || '')}
    </div>
    <h3 class="detail-title">${escapeHtml(issue.title)}</h3>
    <div class="detail-tags">
      <span class="badge ${type}">${categoryLabel[type] || '미분류'}</span>
      <span class="badge ${level}">${priorityLabel[level] || '미정'}</span>
      <span class="status ${done ? 'done' : 'pending'}">
        ${done ? '분석 완료' : '분석 대기'}
      </span>
    </div>
    <div class="drawer-section">
      <h4 class="drawer-section-title">${icon('circle')} 이슈 설명</h4>
      <div class="detail-body">${escapeHtml(issue.body || '내용 없음')}</div>
    </div>
    <div class="drawer-section">
      <h4 class="drawer-section-title">${icon('spark')} AI 분석 결과</h4>
      ${analysisHtml}
    </div>
  `;

  const link = safeGithubUrl(issue.html_url);
  $('#drawerGithub').hidden = !link;
  $('#drawerGithub').dataset.url = link || '';
  $('#analyzeLabel').textContent = done ? '다시 분석' : 'AI 분석 실행';
  $('#analyzeBtn').dataset.force = done ? 'true' : 'false';
  const applyButton = $('#applyAnalysisBtn');
  if (applyButton) applyButton.addEventListener('click', applyAnalysis);
  const discordButton = $('#retryDiscordBtn');
  if (discordButton) discordButton.addEventListener('click', retryDiscord);
}

// GPT 이슈 분석
async function runAnalysis() {
  if (state.busy || state.selected == null || !state.connected || loggingOut) return;

  const number = state.selected;
  const id = connectionId;
  state.busy = true;

  const button = $('#analyzeBtn');
  button.disabled = true;
  $('#analyzeLabel').textContent = '분석 중...';

  try {
    const result = await api(`/api/issues/${number}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ force: button.dataset.force === 'true' }),
      timeoutMs: 120000
    });

    if (loggingOut || id !== connectionId) return;

    if (result.skipped) {
      toast(result.reason || '분석을 건너뛰었습니다.', 'error');
    } else {
      state.analysisHistoryId = result.history_id;
      result.issue.analysis_id = result.history_id;
      result.issue.analysis_comparison = result.comparison || null;
      toast(result.applied
        ? '분석 결과를 저장했습니다. 적용할 항목을 선택해 주세요.'
        : '분석 결과를 저장했습니다. GitHub에 반영하려면 승인해 주세요.'
      );

      await refresh(true);

      if (!loggingOut && id === connectionId &&
          state.selected === number && result.issue) {
        drawDetail(result.issue);
      }
    }
  } catch (err) {
    if (!loggingOut && id === connectionId) {
      toast(err.message, 'error');
      $('#analyzeLabel').textContent = 'AI 분석 실행';
    }
  } finally {
    if (id === connectionId) {
      state.busy = false;
      button.disabled = false;
    }
  }
}

async function applyAnalysis() {
  const number = state.selected;
  const historyId = state.analysisHistoryId;
  if (!number || !historyId) return;
  const applyLabels = $('#applyRecommendedLabels')?.checked || false;
  const postComment = $('#applyAnalysisComment')?.checked || false;
  if (!applyLabels && !postComment) {
    toast('GitHub에 반영할 항목을 선택해 주세요.', 'error');
    return;
  }
  if (!window.confirm('선택한 분석 결과를 실제 GitHub 이슈에 반영할까요?')) return;
  const button = $('#applyAnalysisBtn');
  button.disabled = true;
  try {
    const outcome = await api(`/api/issues/${number}/apply`, {
      method: 'POST',
      body: JSON.stringify({
        history_id: historyId, apply_labels: applyLabels, post_comment: postComment
      }),
      timeoutMs: 60000
    });
    toast(outcome.status === 'applied' ? 'GitHub에 적용했습니다.' :
      outcome.status === 'partial' ? '일부 항목만 적용되었습니다.' : 'GitHub 반영에 실패했습니다.',
    outcome.status === 'applied' ? 'success' : 'error');
    if (outcome.notification_error) toast(outcome.notification_error, 'error');
    await refresh(true);
    if (outcome.issue) {
      outcome.issue.analysis_id = historyId;
      drawDetail(outcome.issue);
    }
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function retryDiscord() {
  if (!state.analysisHistoryId) return;
  try {
    const result = await api(`/api/analysis-history/${state.analysisHistoryId}/retry-discord`, {
      method: 'POST', body: JSON.stringify({})
    });
    toast(result.message || 'Discord 알림을 재전송했습니다.');
    await openDrawer(state.selected);
  } catch (error) {
    toast(error.message, 'error');
  }
}

// 이벤트 등록
function registerEvents() {
  $$('[data-nav]').forEach(button => {
    button.addEventListener('click', () => selectView(button.dataset.nav));
  });

  $$('[data-go]').forEach(button => {
    button.addEventListener('click', () => selectView(button.dataset.go));
  });

  [$('#refreshBtn'), $('#issuesRefresh')].forEach(button => {
    button.addEventListener('click', () => refresh());
  });

  [$('#heroCreate'), $('#newIssueBtn')].forEach(button => {
    button.addEventListener('click', openGithubNewIssue);
  });

  $('#mobileMenu').addEventListener('click', () => {
    $('#sidebar').classList.toggle('mobile-open');
  });

  $('#issueSearch').addEventListener('input', renderIssues);
  $('#issueFilter').addEventListener('change', renderIssues);

  $('#closeDrawer').addEventListener('click', closeDrawer);
  $('#drawerOverlay').addEventListener('click', closeDrawer);
  $('#analyzeBtn').addEventListener('click', runAnalysis);

  $('#drawerGithub').addEventListener('click', () => {
    const url = $('#drawerGithub').dataset.url;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  });

  $('#selectRepoTab').addEventListener('click', () => {
    showRepositoryTab('select');
    loadRepositories();
  });

  $('#createRepoTab').addEventListener('click', () => {
    showRepositoryTab('create');
  });

  $('#setupRepoSearch').addEventListener('input', renderRepositoryList);
  $('#createRepoForm').addEventListener('submit', createRepository);
  $('#setupForm').addEventListener('submit', connectWorkspace);
  $('#integrationSettingsForm').addEventListener('submit', saveIntegrationSettings);
  $('#testOpenaiBtn').addEventListener('click', () => testIntegration('/api/settings/test-openai'));
  $('#testDiscordBtn').addEventListener('click', () => testIntegration('/api/settings/test-discord'));

  $('#changeRepoBtn').addEventListener('click', () => {
    if (loggingOut) return;
    state.selectedRepo = '';
    showSetupStep('setupRepository');
    loadRepositories();
  });

  $('#disconnectBtn').addEventListener('click', disconnectWorkspace);

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeDrawer();
  });
}

// 앱 시작
async function start() {
  registerEvents();

  $('#todayDate').textContent = new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric', month: 'long', day: 'numeric'
  }).format(new Date());

  const hash = (location.hash || '').slice(1);
  const allowed = ['dashboard', 'issues', 'workflow', 'settings'];

  selectView(allowed.includes(hash) ? hash : 'dashboard');
  showSetupStep('setupLogin');

  try {
    await checkLogin();
  } catch (err) {
    if (!loggingOut) {
      showSetupStep('setupLogin');
      toast(err.message, 'error');
    }
  }
}

document.addEventListener('DOMContentLoaded', start);
