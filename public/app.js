
/* IssueFlow AI - GitHub App 기반 대시보드 */

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const state = {
  config: null, issues: [], activity: [], selected: null,
  busy: false, view: 'dashboard', repositories: [], assignees: [],
  selectedIssues: new Set(), issueDisplay: 'list',
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

function markdownDisplaySource(value) {
  // Hide only IssueFlow's own leading analysis payload; keep the original string
  // untouched for analysis recovery and for editing the raw GitHub comment.
  return String(value ?? '')
    .replace(/<!--\s*issueflow-ai:analysis:v1\s+[\s\S]*?-->/g, '');
}

function appendMarkdownInline(parent, source, depth = 0) {
  const text = String(source ?? '');
  if (depth > 12) {
    parent.appendChild(document.createTextNode(text));
    return;
  }
  let index = 0;
  const appendText = value => {
    if (value) parent.appendChild(document.createTextNode(value));
  };
  const safeHref = value => {
    try {
      const url = new URL(value, window.location.href);
      return ['https:', 'http:', 'mailto:'].includes(url.protocol)
        && !url.username && !url.password ? url.href : null;
    } catch (_) {
      return null;
    }
  };
  const appendLink = (label, href) => {
    const safe = safeHref(href);
    if (!safe) {
      appendText(`[${label}](${href})`);
      return;
    }
    const anchor = document.createElement('a');
    anchor.href = safe;
    anchor.rel = 'noopener noreferrer';
    anchor.target = '_blank';
    appendTextToInline(anchor, label);
    parent.appendChild(anchor);
  };

  while (index < text.length) {
    // Inline code. Variable-size backtick delimiters follow Markdown semantics.
    if (text[index] === '`') {
      const run = text.slice(index).match(/^`+/)[0];
      const end = text.indexOf(run, index + run.length);
      if (end >= 0) {
        const code = document.createElement('code');
        code.textContent = text.slice(index + run.length, end).replace(/\n/g, ' ');
        parent.appendChild(code);
        index = end + run.length;
        continue;
      }
    }

    // Markdown links; unsafe protocols are rendered as inert text.
    if (text[index] === '[') {
      const labelEnd = text.indexOf('](', index + 1);
      const hrefEnd = labelEnd >= 0 ? text.indexOf(')', labelEnd + 2) : -1;
      if (labelEnd >= 0 && hrefEnd >= 0) {
        appendLink(
          text.slice(index + 1, labelEnd),
          text.slice(labelEnd + 2, hrefEnd).trim().replace(/^<|>$/g, '')
        );
        index = hrefEnd + 1;
        continue;
      }
    }

    let delimiter = null;
    let tagName = null;
    if (text.startsWith('**', index) || text.startsWith('__', index)) {
      delimiter = text.slice(index, index + 2);
      tagName = 'strong';
    } else if (text[index] === '*' || text[index] === '_') {
      delimiter = text[index];
      tagName = 'em';
    }
    if (delimiter) {
      const end = text.indexOf(delimiter, index + delimiter.length);
      if (end > index + delimiter.length) {
        const node = document.createElement(tagName);
        appendMarkdownInline(node, text.slice(index + delimiter.length, end), depth + 1);
        parent.appendChild(node);
        index = end + delimiter.length;
        continue;
      }
    }

    // Plain URLs are convenient in issue comments; trim punctuation from links.
    const bareUrl = text.slice(index).match(/^https?:\/\/[^\s<]+/);
    if (bareUrl) {
      const match = bareUrl[0];
      const url = match.replace(/[.,!?;:]+$/, '');
      appendLink(url, url);
      appendText(match.slice(url.length));
      index += match.length;
      continue;
    }

    // Move to the next possible token while keeping all source text as text nodes.
    let next = text.length;
    for (const candidate of ['`', '[', '*', '_', 'http://', 'https://']) {
      const found = text.indexOf(candidate, index + 1);
      if (found >= 0 && found < next) next = found;
    }
    appendText(text.slice(index, next));
    index = next;
  }
}

function appendTextToInline(parent, value) {
  // Link labels remain text-only, preventing nested markup and HTML injection.
  parent.appendChild(document.createTextNode(String(value ?? '')));
}

function markdownBlockStart(line) {
  return /^ {0,3}(?:#{1,6}\s|```|~~~|>\s?|(?:[-+*]|\d+\.)\s|(?:---+|___+|\*\*\*+)\s*$)/.test(line);
}

function appendMarkdownBlocks(container, rawLines, depth = 0) {
  if (depth > 8) {
    const paragraph = document.createElement('p');
    paragraph.textContent = rawLines.join('\n');
    container.appendChild(paragraph);
    return;
  }
  const lines = rawLines;
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index++;
      continue;
    }

    const fence = line.match(/^ {0,3}(`{3,}|~{3,})([A-Za-z0-9_+.-]*)\s*$/);
    if (fence) {
      const marker = fence[1];
      const body = [];
      index++;
      while (index < lines.length && !new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`).test(lines[index])) {
        body.push(lines[index++]);
      }
      if (index < lines.length) index++;
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      if (fence[2]) code.className = `language-${fence[2]}`;
      code.textContent = body.join('\n');
      pre.appendChild(code);
      container.appendChild(pre);
      continue;
    }

    const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const element = document.createElement(`h${heading[1].length}`);
      appendMarkdownInline(element, heading[2]);
      container.appendChild(element);
      index++;
      continue;
    }

    if (/^ {0,3}>\s?/.test(line)) {
      const quoted = [];
      while (index < lines.length && /^ {0,3}>\s?/.test(lines[index])) {
        quoted.push(lines[index++].replace(/^ {0,3}>\s?/, ''));
      }
      const quote = document.createElement('blockquote');
      appendMarkdownBlocks(quote, quoted, depth + 1);
      container.appendChild(quote);
      continue;
    }

    if (/^ {0,3}(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      container.appendChild(document.createElement('hr'));
      index++;
      continue;
    }

    const listStart = line.match(/^ {0,3}([-+*]|\d+\.)\s+(.+)$/);
    if (listStart) {
      const ordered = /^\d/.test(listStart[1]);
      const list = document.createElement(ordered ? 'ol' : 'ul');
      const pattern = ordered ? /^ {0,3}\d+\.\s+(.+)$/ : /^ {0,3}[-+*]\s+(.+)$/;
      while (index < lines.length) {
        const item = lines[index].match(pattern);
        if (!item) break;
        const li = document.createElement('li');
        appendMarkdownInline(li, item[1]);
        list.appendChild(li);
        index++;
      }
      container.appendChild(list);
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length && lines[index].trim() && !markdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index++]);
    }
    if (!paragraphLines.length) {
      // Unknown Markdown constructs remain visible as inert text.
      paragraphLines.push(lines[index++]);
    }
    const paragraph = document.createElement('p');
    paragraphLines.forEach((paragraphLine, lineIndex) => {
      const hardBreak = /(?: {2,}|\\)$/.test(paragraphLine);
      appendMarkdownInline(paragraph, paragraphLine.replace(/(?: {2,}|\\)$/, ''));
      if (lineIndex < paragraphLines.length - 1) {
        if (hardBreak) paragraph.appendChild(document.createElement('br'));
        else paragraph.appendChild(document.createTextNode(' '));
      }
    });
    container.appendChild(paragraph);
  }
}

function renderMarkdown(source) {
  const root = document.createElement('div');
  root.className = 'markdown-body';
  appendMarkdownBlocks(root, markdownDisplaySource(source).replace(/\r\n?/g, '\n').split('\n'));
  return root;
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
  const label = (issue.labels || []).find(v => v.startsWith('priority:'));
  const value = label ? label.split(':')[1] : 'none';
  if (['high', 'medium', 'low'].includes(value)) return value;
  return ['high', 'medium', 'low'].includes(issue.analysis?.priority)
    ? issue.analysis.priority : 'none';
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
  ['setupLoading', 'setupLogin', 'setupOauth', 'setupRepository', 'setupCredentials'].forEach(id => {
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
    assignees: [], selectedIssues: new Set(), issueDisplay: 'list',
    selectedRepo: '', analysisHistoryId: null,
    connected: false
  });

  clearGithubUser();
  $('#setupForm').reset();
  $('#setupError').textContent = '';
  $('#repoSetupError').textContent = '';
  $('#setupRepoSearch').value = '';
  $('#issueSearch').value = '';
  ['issueStateFilter', 'issueTypeFilter', 'issuePriorityFilter', 'issueAssigneeFilter']
    .forEach(id => { if ($(`#${id}`)) $(`#${id}`).value = 'all'; });
  $('#issueSort').value = 'newest';

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
  if (loggingOut) return null;

  const id = ++authCheckId;
  const connection = connectionId;
  const result = await api('/api/auth/me');

  // 오래된 인증 확인 결과는 화면에 적용하지 않음
  if (loggingOut || id !== authCheckId || connection !== connectionId) return null;

  if (!result.authenticated) {
    state.selectedRepo = '';
    state.repositories = [];
    clearGithubUser();
    showSetupStep('setupLogin');
    return false;
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
    return true;
  }

  state.selectedRepo = '';
  $('#selectedRepoName').textContent = '';
  showSetupStep('setupRepository');
  await loadRepositories();
  return true;
}

async function retryLoginCheck() {
  const button = $('#retryAuthCheck');
  const message = $('#setupLoadingMessage');
  button.disabled = true;
  button.hidden = true;
  message.textContent = '로그인 상태를 다시 확인하고 있습니다.';
  try {
    await checkLogin();
  } catch (error) {
    message.textContent = '로그인 상태를 확인할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요.';
    button.hidden = false;
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
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
      body: JSON.stringify({
        name, description, private: isPrivate,
        initialize_readme: $('#newRepoReadme').checked,
        initialize_labels: $('#newRepoLabels').checked,
        initialize_templates: $('#newRepoTemplates').checked
      }),
      timeoutMs: 45000
    });

    if (loggingOut || id !== connectionId) return;

    toast(`${result.repo} 저장소가 생성되었습니다.`);
    if (result.initialization?.errors?.length) {
      window.alert(`저장소는 생성됐지만 초기 설정 일부에 실패했습니다.\n${result.initialization.errors.join('\n')}`);
    }
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
  const [config, issues, activity, analysis, assignees] = await Promise.all([
    api('/api/config'), api('/api/issues'), api('/api/activity'),
    api('/api/analysis-history'), api('/api/assignees').catch(() => ({ items: [] }))
  ]);
  state.config = config;
  state.issues = issues.items || [];
  state.activity = activity.items || [];
  state.assignees = assignees.items || [];
  state.connected = true;
  state.selectedRepo = config.repo;

  renderConfig();
  renderIssues();
  renderAssigneeFilters();
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
  const assigned = (issue.assignees || []).map(escapeHtml).join(', ') || '미지정';

  return `
    <tr class="issue-row" data-issue="${number}" tabindex="0">
      ${full ? `<td><input type="checkbox" class="issue-select" data-issue-select="${number}" aria-label="이슈 #${number} 선택" ${state.selectedIssues.has(number) ? 'checked' : ''}></td>` : ''}
      <td>
        <span class="issue-name" title="${name}">${name}</span>
        <span class="issue-id">#${number} · ${escapeHtml(issue.repository || '')}</span>
      </td>
      <td><span class="badge ${type}">${categoryLabel[type] || '미분류'}</span></td>
      <td><span class="badge ${level}">${priorityLabel[level] || '미정'}</span></td>
      <td><span class="status ${issue.state === 'closed' ? 'done' : 'pending'}">${issue.state === 'closed' ? 'Closed' : 'Open'}</span></td>
      ${full ? `<td>${assigned}</td><td>${escapeHtml(miniDate(issue.created_at))}</td>` : ''}
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
  const stateFilter = $('#issueStateFilter').value;
  const typeFilter = $('#issueTypeFilter').value;
  const priorityFilter = $('#issuePriorityFilter').value;
  const assigneeFilter = $('#issueAssigneeFilter').value;

  const visible = state.issues.filter(issue => {
    const matchesSearch = !query ||
      String(issue.title || '').toLowerCase().includes(query) ||
      String(issue.body || '').toLowerCase().includes(query);
    const assigned = issue.assignees || [];
    return matchesSearch
      && (stateFilter === 'all' || issue.state === stateFilter)
      && (typeFilter === 'all' || category(issue) === typeFilter)
      && (priorityFilter === 'all' || priority(issue) === priorityFilter)
      && (assigneeFilter === 'all'
        || (assigneeFilter === 'unassigned' && !assigned.length)
        || assigned.includes(assigneeFilter));
  }).sort((a, b) => {
    const delta = new Date(a.created_at || 0) - new Date(b.created_at || 0);
    return $('#issueSort').value === 'oldest' ? delta : -delta;
  });

  $('#allIssues').innerHTML = visible.length
    ? visible.map(issue => issueRow(issue, true)).join('')
    : '<tr><td colspan="8" class="empty-cell">검색 조건에 맞는 이슈가 없습니다.</td></tr>';

  $('#issueBoardView').innerHTML = renderBoard(visible);
  $('#issueListView').hidden = state.issueDisplay !== 'list';
  $('#issueBoardView').hidden = state.issueDisplay !== 'board';
  $('#listViewBtn').classList.toggle('active', state.issueDisplay === 'list');
  $('#boardViewBtn').classList.toggle('active', state.issueDisplay === 'board');
  $('#selectAllIssues').checked = visible.length > 0
    && visible.every(issue => state.selectedIssues.has(issue.number));
  renderBulkTools();

  $('#listSummary').textContent =
    `총 ${state.issues.length}개 중 ${visible.length}개 표시`;

  $$('.issue-row').forEach(row => {
    const number = Number(row.dataset.issue);
    row.addEventListener('click', () => openDrawer(number));
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter') openDrawer(number);
    });
  });
  $$('.issue-select').forEach(box => box.addEventListener('click', event => event.stopPropagation()));
  $$('.issue-select').forEach(box => box.addEventListener('change', () => {
    const number = Number(box.dataset.issueSelect);
    if (box.checked) state.selectedIssues.add(number);
    else state.selectedIssues.delete(number);
    renderBulkTools();
  }));
  $$('[data-board-status]').forEach(select => select.addEventListener('change', () => {
    changeBoardStatus(Number(select.dataset.issue), select.value);
  }));
  $$('[data-open-issue]').forEach(button => button.addEventListener('click', () => {
    openDrawer(Number(button.dataset.openIssue));
  }));
}

function renderAssigneeFilters() {
  const select = $('#issueAssigneeFilter');
  select.innerHTML = '<option value="all">모든 담당자</option><option value="unassigned">미지정</option>'
    + state.assignees.map(person =>
      `<option value="${escapeHtml(person.login)}">${escapeHtml(person.login)}</option>`
    ).join('');
}

function boardStatus(issue) {
  const labels = issue.labels || [];
  if (labels.includes('status:in-progress')) return 'in_progress';
  if (labels.includes('status:done')) return 'done';
  if (labels.includes('status:todo')) return 'todo';
  if (issue.state === 'closed') return 'done';
  return 'todo';
}

function renderBoard(issues) {
  const columns = [
    ['todo', 'To Do'], ['in_progress', 'In Progress'], ['done', 'Done']
  ];
  return columns.map(([status, title]) => {
    const cards = issues.filter(issue => boardStatus(issue) === status);
    return `<section class="kanban-column"><div class="kanban-head"><strong>${title}</strong><span>${cards.length}</span></div>${
      cards.map(issue => `<article class="kanban-card" data-issue="${Number(issue.number)}">
        <button type="button" class="kanban-card-title" data-open-issue="${Number(issue.number)}">#${Number(issue.number)} · ${escapeHtml(issue.title)}</button>
        <p>${escapeHtml((issue.body || '').slice(0, 150))}</p>
        <small>${issue.state === 'closed' ? 'Closed' : 'Open'} · ${escapeHtml((issue.assignees || []).join(', ') || '미지정')}</small>
        <label>상태 <select data-board-status data-issue="${Number(issue.number)}">
          <option value="todo" ${status === 'todo' ? 'selected' : ''}>To Do</option>
          <option value="in_progress" ${status === 'in_progress' ? 'selected' : ''}>In Progress</option>
          <option value="done" ${status === 'done' ? 'selected' : ''}>Done</option>
        </select></label>
      </article>`).join('') || '<p class="subtle-empty">이슈 없음</p>'
    }</section>`;
  }).join('');
}

function renderBulkTools() {
  const count = state.selectedIssues.size;
  $('#bulkTools').hidden = count === 0;
  $('#selectedIssueCount').textContent = `${count}개 선택`;
}

function configureBulkValue() {
  const action = $('#bulkAction').value;
  const value = $('#bulkValue');
  if (action === 'assign') {
    value.innerHTML = '<option value="">담당자 선택</option><option value="__none__">담당자 없음</option>'
      + state.assignees.map(item => `<option value="${escapeHtml(item.login)}">${escapeHtml(item.login)}</option>`).join('');
    value.hidden = false;
  } else if (action === 'priority') {
    value.innerHTML = '<option value="">우선순위 선택</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>';
    value.hidden = false;
  } else {
    value.hidden = true;
    value.innerHTML = '';
  }
}

async function runBulkIssueAction() {
  const numbers = [...state.selectedIssues];
  const action = $('#bulkAction').value;
  const value = $('#bulkValue').value;
  if (!numbers.length || !action) {
    toast('이슈와 일괄 작업을 선택해 주세요.', 'error');
    return;
  }
  if ((action === 'assign' || action === 'priority') && !value) {
    toast('변경할 값을 선택해 주세요.', 'error');
    return;
  }
  const label = { close: '닫기', reopen: '다시 열기', assign: '담당자 변경', priority: '우선순위 변경' }[action];
  if (!window.confirm(`선택한 ${numbers.length}개 이슈의 ${label} 작업을 실행할까요? GitHub에 실제 반영됩니다.`)) return;
  const body = { numbers, action };
  if (action === 'assign') body.assignees = value === '__none__' ? [] : [value];
  if (action === 'priority') body.priority = value;
  const button = $('#runBulkAction');
  button.disabled = true;
  try {
    const result = await api('/api/issues/bulk', {
      method: 'POST', body: JSON.stringify(body), timeoutMs: 120000
    });
    state.selectedIssues.clear();
    $('#bulkAction').value = '';
    configureBulkValue();
    toast(`${result.succeeded}개 성공, ${result.failed}개 실패`,
      result.failed ? 'error' : 'success');
    const failures = result.items.filter(item => !item.ok)
      .map(item => `#${item.number}: ${item.error}`).join('\n');
    if (failures) window.alert(`일부 작업이 실패했습니다.\n${failures}`);
    await refresh(true);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
    renderIssues();
  }
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
  selectView('issues');
  $('#createIssueForm').hidden = false;
  $('#createIssueTitle').focus();
}

async function submitNewIssue(event) {
  event.preventDefault();
  const title = $('#createIssueTitle').value.trim();
  const body = $('#createIssueBody').value;
  if (!title) return;
  if (!window.confirm(`"${title}" 이슈를 ${state.config.repo} 저장소에 등록할까요?`)) return;
  const button = $('#createIssueForm').querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const result = await api('/api/issues', {
      method: 'POST', body: JSON.stringify({ title, body }), timeoutMs: 60000
    });
    $('#createIssueForm').reset();
    $('#createIssueForm').hidden = true;
    toast('GitHub에 이슈를 등록했습니다. AI 분석은 자동 실행되지 않습니다.');
    await refresh(true);
    openDrawer(result.item.number);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
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
          <div id="analysisCommentPreview" class="detail-body markdown-preview"></div>
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
    <details class="drawer-section issue-management" open>
      <summary class="drawer-section-title">이슈 관리</summary>
      <form id="editIssueForm">
        <label for="editIssueTitle">제목</label>
        <input id="editIssueTitle" maxlength="256" value="${escapeHtml(issue.title)}" required>
        <label for="editIssueBody">본문</label>
        <textarea id="editIssueBody" rows="5" maxlength="65536">${escapeHtml(issue.body || '')}</textarea>
        <button class="btn btn-outline btn-sm" type="submit">제목·본문 저장</button>
      </form>
      <div class="issue-admin-row">
        <button type="button" class="btn btn-outline btn-sm" id="toggleIssueStateBtn">${issue.state === 'closed' ? '이슈 다시 열기' : '이슈 닫기'}</button>
        <label>우선순위 <select id="issuePrioritySelect">
          <option value="high" ${level === 'high' ? 'selected' : ''}>High</option>
          <option value="medium" ${level === 'medium' ? 'selected' : ''}>Medium</option>
          <option value="low" ${level === 'low' ? 'selected' : ''}>Low</option>
        </select></label>
      </div>
      <label for="issueAssignees">담당자</label>
      <select id="issueAssignees" multiple size="4">${
        state.assignees.map(person =>
          `<option value="${escapeHtml(person.login)}" ${(issue.assignees || []).includes(person.login) ? 'selected' : ''}>${escapeHtml(person.login)}</option>`
        ).join('')
      }</select>
      <button type="button" class="btn btn-outline btn-sm" id="saveIssueAssignees">담당자 저장</button>
    </details>
    <section class="drawer-section">
      <h4 class="drawer-section-title">댓글</h4>
      <div id="issueComments"><p class="subtle-empty">댓글을 불러오는 중입니다.</p></div>
      <form id="newCommentForm" class="comment-form">
        <label for="newCommentBody">댓글 작성</label>
        <textarea id="newCommentBody" rows="3" maxlength="65536" required></textarea>
        <button class="btn btn-outline btn-sm" type="submit">댓글 등록</button>
      </form>
    </section>
    <div class="drawer-section">
      <h4 class="drawer-section-title">${icon('spark')} AI 분석 결과</h4>
      ${analysisHtml}
    </div>
  `;

  const preview = $('#analysisCommentPreview');
  if (preview) preview.appendChild(renderMarkdown(
    analysis.comment_preview || analysis.summary || '-'
  ));

  const link = safeGithubUrl(issue.html_url);
  $('#drawerGithub').hidden = !link;
  $('#drawerGithub').dataset.url = link || '';
  $('#analyzeLabel').textContent = done ? '다시 분석' : 'AI 분석 실행';
  $('#analyzeBtn').dataset.force = done ? 'true' : 'false';
  $('#editIssueForm').addEventListener('submit', saveIssueEdits);
  $('#toggleIssueStateBtn').addEventListener('click', () => changeIssueState(issue));
  $('#issuePrioritySelect').addEventListener('change', changeIssuePriority);
  $('#saveIssueAssignees').addEventListener('click', saveIssueAssignees);
  $('#newCommentForm').addEventListener('submit', createIssueComment);
  loadComments(issue.number);
  const applyButton = $('#applyAnalysisBtn');
  if (applyButton) applyButton.addEventListener('click', applyAnalysis);
  const discordButton = $('#retryDiscordBtn');
  if (discordButton) discordButton.addEventListener('click', retryDiscord);
}

async function reloadSelectedIssue() {
  const number = state.selected;
  if (!number) return;
  await refresh(true);
  await openDrawer(number);
}

async function saveIssueEdits(event) {
  event.preventDefault();
  const number = state.selected;
  const title = $('#editIssueTitle').value.trim();
  const body = $('#editIssueBody').value;
  try {
    await api(`/api/issues/${number}`, {
      method: 'PATCH', body: JSON.stringify({ title, body })
    });
    toast('이슈 내용을 저장했습니다.');
    await reloadSelectedIssue();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function changeIssueState(issue) {
  const next = issue.state === 'closed' ? 'open' : 'closed';
  const label = next === 'closed' ? '닫기' : '다시 열기';
  if (!window.confirm(`이 이슈를 ${label}할까요? 실제 GitHub 이슈 상태가 변경됩니다.`)) return;
  try {
    await api(`/api/issues/${issue.number}/state`, {
      method: 'POST', body: JSON.stringify({ state: next })
    });
    toast(`이슈를 ${next === 'closed' ? '닫았습니다.' : '다시 열었습니다.'}`);
    await reloadSelectedIssue();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function changeIssuePriority() {
  const number = state.selected;
  try {
    await api(`/api/issues/${number}/priority`, {
      method: 'PUT',
      body: JSON.stringify({ priority: $('#issuePrioritySelect').value })
    });
    toast('우선순위 라벨을 변경했습니다.');
    await reloadSelectedIssue();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function saveIssueAssignees() {
  const selected = [...$('#issueAssignees').selectedOptions].map(option => option.value);
  try {
    await api(`/api/issues/${state.selected}/assignees`, {
      method: 'PUT', body: JSON.stringify({ assignees: selected })
    });
    toast('담당자를 변경했습니다.');
    await reloadSelectedIssue();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function loadComments(number) {
  try {
    const result = await api(`/api/issues/${number}/comments`);
    if (state.selected !== number) return;
    const parent = $('#issueComments');
    parent.replaceChildren();
    if (!result.items.length) {
      const empty = document.createElement('p');
      empty.className = 'subtle-empty';
      empty.textContent = '등록된 댓글이 없습니다.';
      parent.appendChild(empty);
      return;
    }
    result.items.forEach(comment => parent.appendChild(createCommentCard(number, comment)));
  } catch (error) {
    if (state.selected === number) $('#issueComments').textContent = error.message;
  }
}

function createCommentCard(number, comment) {
  const article = document.createElement('article');
  article.className = 'issue-comment';
  const header = document.createElement('div');
  header.className = 'issue-comment-header';
  const author = document.createElement('strong');
  author.textContent = comment.user || 'GitHub 사용자';
  const date = document.createElement('small');
  date.textContent = formatDate(comment.created_at);
  header.append(author, date);
  article.append(header, renderMarkdown(comment.body));

  const actions = document.createElement('div');
  actions.className = 'comment-actions';
  const edit = document.createElement('button');
  edit.type = 'button';
  edit.className = 'btn btn-outline btn-sm';
  edit.textContent = '수정';
  edit.addEventListener('click', () => editIssueComment(number, comment, article));
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'btn btn-outline btn-sm';
  remove.textContent = '삭제';
  remove.addEventListener('click', () => deleteIssueComment(number, Number(comment.id)));
  actions.append(edit, remove);
  article.appendChild(actions);
  return article;
}

async function createIssueComment(event) {
  event.preventDefault();
  const body = $('#newCommentBody').value.trim();
  if (!body) return;
  try {
    await api(`/api/issues/${state.selected}/comments`, {
      method: 'POST', body: JSON.stringify({ body })
    });
    $('#newCommentBody').value = '';
    toast('댓글을 등록했습니다.');
    await loadComments(state.selected);
  } catch (error) {
    toast(error.message, 'error');
  }
}

function splitAnalysisMetadata(rawBody) {
  const raw = String(rawBody ?? '');
  const prefix = raw.match(
    /^(<!--\s*issueflow-ai:analysis:v1\s+\{[^\r\n]*\}\s*-->)(\r?\n)?/
  );
  return prefix
    ? { metadataPrefix: prefix[0], editableBody: raw.slice(prefix[0].length) }
    : { metadataPrefix: '', editableBody: raw };
}

function editIssueComment(number, comment, article) {
  const { metadataPrefix, editableBody } = splitAnalysisMetadata(comment.body);
  const editor = document.createElement('div');
  editor.className = 'comment-editor';

  const textarea = document.createElement('textarea');
  textarea.className = 'comment-editor-source';
  textarea.setAttribute('aria-label', 'Markdown 댓글 작성');
  textarea.spellcheck = false;
  textarea.value = editableBody;

  const actions = document.createElement('div');
  actions.className = 'comment-editor-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-outline btn-sm';
  cancel.textContent = '취소';
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'btn btn-primary btn-sm';
  save.textContent = '저장';
  actions.append(cancel, save);
  editor.append(textarea, actions);
  article.replaceChildren(editor);
  textarea.focus();

  cancel.addEventListener('click', () => {
    // Discard only the local editor buffer. No GitHub request is made.
    article.replaceWith(createCommentCard(number, comment));
  });
  save.addEventListener('click', async () => {
    if (!metadataPrefix && !textarea.value.trim()) {
      toast('댓글 내용을 입력해 주세요.', 'error');
      return;
    }
    save.disabled = true;
    try {
      // Keep IssueFlow's hidden JSON marker byte-for-byte so existing analysis
      // history recovery continues to work after editing the visible Markdown.
      const body = metadataPrefix + textarea.value;
      await api(`/api/issues/${number}/comments/${comment.id}`, {
        method: 'PATCH', body: JSON.stringify({ body })
      });
      toast('댓글을 수정했습니다.');
      await loadComments(number);
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      save.disabled = false;
    }
  });
}

async function deleteIssueComment(number, commentId) {
  if (!window.confirm('이 댓글을 GitHub에서 삭제할까요? 이 작업은 되돌릴 수 없습니다.')) return;
  try {
    await api(`/api/issues/${number}/comments/${commentId}`, {
      method: 'DELETE', body: JSON.stringify({})
    });
    toast('댓글을 삭제했습니다.');
    await loadComments(number);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function changeBoardStatus(number, status) {
  try {
    await api(`/api/issues/${number}/board-status`, {
      method: 'PUT', body: JSON.stringify({ status })
    });
    toast('칸반 상태를 GitHub 라벨에 저장했습니다.');
    await refresh(true);
  } catch (error) {
    toast(error.message, 'error');
    renderIssues();
  }
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

  $('#welcomeStartBtn').addEventListener('click', () => {
    $('#oauthLoginError').hidden = true;
    $('#oauthLoginError').textContent = '';
    showSetupStep('setupOauth');
  });
  $('#backToWelcomeBtn').addEventListener('click', () => {
    $('#oauthLoginError').hidden = true;
    $('#oauthLoginError').textContent = '';
    showSetupStep('setupLogin');
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
  ['issueStateFilter', 'issueTypeFilter', 'issuePriorityFilter',
    'issueAssigneeFilter', 'issueSort'].forEach(id =>
    $(`#${id}`).addEventListener('change', renderIssues)
  );
  $('#resetIssueFilters').addEventListener('click', () => {
    $('#issueSearch').value = '';
    $('#issueStateFilter').value = 'all';
    $('#issueTypeFilter').value = 'all';
    $('#issuePriorityFilter').value = 'all';
    $('#issueAssigneeFilter').value = 'all';
    $('#issueSort').value = 'newest';
    state.selectedIssues.clear();
    renderIssues();
  });
  $('#listViewBtn').addEventListener('click', () => {
    state.issueDisplay = 'list';
    renderIssues();
  });
  $('#boardViewBtn').addEventListener('click', () => {
    state.issueDisplay = 'board';
    renderIssues();
  });
  $('#selectAllIssues').addEventListener('change', event => {
    $$('.issue-select').forEach(box => {
      const number = Number(box.dataset.issueSelect);
      if (event.target.checked) state.selectedIssues.add(number);
      else state.selectedIssues.delete(number);
    });
    renderIssues();
  });
  $('#bulkAction').addEventListener('change', configureBulkValue);
  $('#runBulkAction').addEventListener('click', runBulkIssueAction);

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
  $('#createIssueForm').addEventListener('submit', submitNewIssue);
  $('#cancelCreateIssue').addEventListener('click', () => {
    $('#createIssueForm').reset();
    $('#createIssueForm').hidden = true;
  });
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
  $('#retryAuthCheck').addEventListener('click', retryLoginCheck);

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
  showSetupStep('setupLoading');
  const oauthError = new URLSearchParams(location.search).get('auth_error');

  try {
    const authenticated = await checkLogin();
    if (authenticated === false && oauthError) {
      const messages = {
        state: '로그인 검증이 만료되었거나 브라우저 세션이 달라졌습니다. 다시 시도해 주세요.',
        denied: 'GitHub 로그인이 취소되었습니다. 원하면 다시 시도할 수 있습니다.',
        config: 'GitHub 로그인 설정을 확인해 주세요.',
        github: 'GitHub 인증을 완료하지 못했습니다. 다시 시도해 주세요.'
      };
      $('#oauthLoginError').textContent =
        messages[oauthError] || messages.github;
      $('#oauthLoginError').hidden = false;
      showSetupStep('setupOauth');
      const url = new URL(location.href);
      url.searchParams.delete('auth_error');
      history.replaceState(history.state, '', `${url.pathname}${url.search}${url.hash}`);
    }
  } catch (err) {
    if (!loggingOut) {
      $('#setupLoadingMessage').textContent =
        '로그인 상태를 확인할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요.';
      $('#retryAuthCheck').hidden = false;
      showSetupStep('setupLoading');
      toast(err.message, 'error');
    }
  }
}

document.addEventListener('DOMContentLoaded', start);
