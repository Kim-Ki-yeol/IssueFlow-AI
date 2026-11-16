/* 홈페이지 화면 동작. API에서 받은 글은 화면에 표시하기 전에 이스케이프 처리함. */
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { config: null, issues: [], activity: [], selected: null, busy: false, view: 'dashboard' };
const icons = {check:'i-check',spark:'i-spark',circle:'i-circle',github:'i-github',bell:'i-bell'};
function icon(name, cls='icon'){return `<svg class="${cls}" aria-hidden="true"><use href="#${icons[name] || name}"/></svg>`;}
function escapeHtml(v){return String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function safeGithubUrl(value){const v=String(value||'');return /^https:\/\/github\.com\/[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+\/(issues|pull)\/\d+$/.test(v)?v:null;}
function formatDate(value){if(!value)return '-';const d=new Date(value);return Number.isNaN(d.getTime())?'-':new Intl.DateTimeFormat('ko-KR',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(d);}
function miniDate(value){if(!value)return '-';const d=new Date(value);return Number.isNaN(d.getTime())?'-':new Intl.DateTimeFormat('ko-KR',{month:'short',day:'numeric'}).format(d);}
function category(issue){const labels=issue.labels||[];if(labels.includes('bug'))return 'bug';if(labels.includes('enhancement'))return 'feature';if(labels.includes('documentation'))return 'documentation';if(labels.includes('question'))return 'question';return issue.analysis?.category||'unclassified';}
function priority(issue){const p=(issue.labels||[]).find(v=>v.startsWith('priority:'));const value=p?p.split(':')[1]:(issue.analysis?.priority||'none');return ['high','medium','low'].includes(value)?value:'none';}
function processed(issue){return (issue.labels||[]).includes('ai:processed')||Boolean(issue.analysis);}
const categoryLabel={bug:'Bug',feature:'Feature',documentation:'Docs',question:'Question',unclassified:'미분류'};
const priorityLabel={high:'높음',medium:'보통',low:'낮음',none:'미정'};
// Flask 서버의 /api/ 경로로 요청 보내는 공통 함수
// GitHub 토큰이나 GPT 키는 여기 넣지 않고 서버 .env에서 관리
// /api/config에서 받은 연결 상태만 화면에 표시
async function api(path,options={}){
 const controller=new AbortController();
 const {timeoutMs=30000,...requestOptions}=options;
 const timeout=setTimeout(()=>controller.abort(),timeoutMs);
 try{const res=await fetch(path,{...requestOptions,headers:{'Content-Type':'application/json',...(requestOptions.headers||{})},signal:controller.signal});
  const data=await res.json().catch(()=>({}));if(!res.ok)throw new Error(data.error||`HTTP ${res.status}`);return data;
 }catch(err){if(err.name==='AbortError')throw new Error('요청 시간이 초과되었습니다. 다시 시도해 주세요.');throw err;}finally{clearTimeout(timeout);}
}
function toast(text,type='success'){
 const parent=$('#toastContainer'),item=document.createElement('div');item.className=`toast ${type}`;item.textContent=text;parent.appendChild(item);setTimeout(()=>item.remove(),4500);
}
function selectView(name){
 const views={dashboard:'대시보드',issues:'이슈 관리',workflow:'자동화 워크플로우',settings:'연동 설정'};
 if(!(name in views))return;state.view=name;
 $$('.view').forEach(v=>v.classList.toggle('active',v.id===`view${name.charAt(0).toUpperCase()+name.slice(1)}`));
 $$('.nav-link').forEach(v=>v.classList.toggle('active',v.dataset.nav===name));$('#breadcrumbTitle').textContent=views[name];$('#sidebar').classList.remove('mobile-open');
 history.replaceState(null,'',`#${name}`);window.scrollTo({top:0,behavior:'instant'});
}
function issueRow(issue,full=false){
 const name=escapeHtml(issue.title),n=Number(issue.number),c=category(issue),p=priority(issue),done=processed(issue);
 return `<tr class="issue-row" data-issue="${n}" tabindex="0" aria-label="이슈 ${n} 상세 보기">
  <td><span class="issue-name" title="${name}">${name}</span><span class="issue-id">#${n} · ${escapeHtml(issue.repository||'')}</span></td>
  <td><span class="badge ${c}">${categoryLabel[c]||'미분류'}</span></td>
  <td><span class="badge ${p}">${priorityLabel[p]||'미정'}</span></td>
  <td><span class="status ${done?'done':'pending'}">${done?'분석 완료':'대기 중'}</span></td>
  ${full?`<td>${escapeHtml(miniDate(issue.created_at))}</td>`:''}
  <td><svg class="icon row-chevron" aria-hidden="true"><use href="#i-arrow"/></svg></td>
 </tr>`;
}
function renderMetrics(){
 const total=state.issues.length,done=state.issues.filter(processed).length,high=state.issues.filter(i=>priority(i)==='high').length;
 $('#metricTotal').textContent=total;$('#metricDone').textContent=done;$('#metricHigh').textContent=high;$('#metricPending').textContent=total-done;
 $('#metricDonePct').textContent=`${total?Math.round(done/total*100):0}% 완료`;
 $('#navCount').textContent=total;$('#issueCount').textContent=total;
}
function attachIssueRows(){
 $$('.issue-row').forEach(row=>{
  row.addEventListener('click',()=>openDrawer(Number(row.dataset.issue)));
  row.addEventListener('keydown',e=>{if(e.key==='Enter')openDrawer(Number(row.dataset.issue));});
 });
}
function renderIssues(){
 renderMetrics();
 const recent=state.issues.slice(0,5);$('#recentIssues').innerHTML=recent.length?recent.map(i=>issueRow(i)).join(''):'<tr><td colspan="5" class="empty-cell">표시할 이슈가 없습니다.</td></tr>';
 const query=$('#issueSearch').value.trim().toLowerCase(),filter=$('#issueFilter').value;
 const visible=state.issues.filter(i=>(!query||i.title.toLowerCase().includes(query)||String(i.number).includes(query))&&(filter==='all'||filter==='pending'&&!processed(i)||filter==='processed'&&processed(i)||filter==='high'&&priority(i)==='high'));
 $('#allIssues').innerHTML=visible.length?visible.map(i=>issueRow(i,true)).join(''):'<tr><td colspan="6" class="empty-cell">검색 조건에 맞는 이슈가 없습니다.</td></tr>';
 $('#listSummary').textContent=`총 ${state.issues.length}개 중 ${visible.length}개 표시`;
 attachIssueRows();
}
function renderActivity(){
 const arr=state.activity;
 $('#activityList').innerHTML=arr.length?arr.map(a=>`<div class="activity-item"><span class="activity-symbol">${icon(a.kind==='created'?'circle':'check')}</span><div><strong>${escapeHtml(a.title)}</strong><p>#${Number(a.issue_number)} · ${escapeHtml(a.detail)}</p><small>${escapeHtml(formatDate(a.created_at))}</small></div></div>`).join(''):'<p class="subtle-empty">아직 처리 기록이 없습니다.<br/>이슈를 분석하면 이곳에 나타납니다.</p>';
}
function integration(iconName,name,description,enabled,status){
 return `<div class="integration-row"><div class="integration-main"><div class="integration-icon">${icon(iconName)}</div><div><strong>${escapeHtml(name)}</strong><p>${escapeHtml(description)}</p></div></div><span class="integration-state ${enabled?'enabled':''}">${escapeHtml(status)}</span></div>`;
}
// 연결 여부만 화면에 표시. API 키는 서버 환경변수에만 저장
function renderConfig(){
 const c=state.config;if(!c)return;
 $('#sidebarMode').textContent='실제 GitHub 연동';
 $('#topMode').textContent='GitHub workspace';
 $('#integrationRows').innerHTML=[
  integration('github','GitHub REST API',c.repo||'저장소 미설정',c.github_enabled,c.github_enabled?'설정됨':'설정 필요'),
  integration('spark','OpenAI GPT API',`모델: ${c.model}`,c.ai_enabled,c.ai_enabled?'설정됨':'설정 필요'),
  integration('bell','Discord Webhook','이슈 분석 알림 (선택)',c.discord_enabled,c.discord_enabled?'설정됨':'미설정'),
  integration('check','GitHub 쓰기 권한','라벨 및 분석 댓글 등록',c.writes_enabled,c.writes_enabled?'활성화':'읽기 전용')
 ].join('');
}
async function refresh(silent=false){
 try{
  const [config,issues,activity]=await Promise.all([api('/api/config'),api('/api/issues'),api('/api/activity')]);
  state.config=config;state.issues=issues.items||[];state.activity=activity.items||[];renderConfig();renderIssues();renderActivity();
  if(!silent)toast('최신 정보를 불러왔습니다.');
 }catch(e){toast(e.message,'error');}
}
function openGithubNewIssue(){
 const repo=state.config?.repo||'';
 if(/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repo)){
  window.open(`https://github.com/${repo}/issues/new`,'_blank','noopener,noreferrer');
 }else toast('GITHUB_REPO 환경변수를 먼저 설정해 주세요.','error');
}
async function openDrawer(number){
 $('#drawerOverlay').hidden=false;$('#detailDrawer').classList.add('open');$('#detailDrawer').setAttribute('aria-hidden','false');
 state.selected=number;$('#drawerContent').innerHTML='<p class="subtle-empty">이슈 상세를 불러오는 중입니다.</p>';
 try{const issue=await api(`/api/issues/${number}`);if(state.selected===number)drawDetail(issue);}catch(e){toast(e.message,'error');$('#drawerContent').innerHTML='<p class="subtle-empty">이슈 상세 정보를 가져올 수 없습니다.</p>';}
}
function closeDrawer(){state.selected=null;$('#drawerOverlay').hidden=true;$('#detailDrawer').classList.remove('open');$('#detailDrawer').setAttribute('aria-hidden','true');}
function drawDetail(issue){
 const done=processed(issue),c=category(issue),p=priority(issue),analysis=issue.analysis;
 const source='GPT 분석';
 let analysisHtml=`<div class="analysis-placeholder">${icon('spark')}아직 분석 결과가 없습니다.<br/>아래의 AI 분석 실행 버튼을 눌러 확인하세요.</div>`;
 if(analysis){analysisHtml=`<div class="analysis-card"><div class="analysis-header"><strong>AI 분석 리포트</strong><span class="badge ${p}">${priorityLabel[p]||'미정'}</span></div><p class="analysis-note">${source} · 진단 결과는 추정이며 실제 원인은 확인이 필요합니다.</p>
  <div class="analysis-meta"><div><small>이슈 유형</small><strong>${categoryLabel[c]||'기타'}</strong></div><div><small>관련 영역</small><strong>${escapeHtml(analysis.area||'-')}</strong></div></div>
  <div class="analysis-detail"><h4>핵심 요약</h4><p>${escapeHtml(analysis.summary||'-')}</p></div>
  <div class="analysis-detail"><h4>추정 원인</h4><p>${escapeHtml(analysis.probable_cause||'-')}</p></div>
  <div class="analysis-detail"><h4>권장 점검 방법</h4><ul>${(analysis.suggestions||[]).map(v=>`<li>${escapeHtml(v)}</li>`).join('')}</ul></div></div>`;}
 $('#drawerContent').innerHTML=`<div class="detail-id">ISSUE #${Number(issue.number)} · ${escapeHtml(issue.repository||'')}</div>
  <h3 class="detail-title">${escapeHtml(issue.title)}</h3><div class="detail-tags"><span class="badge ${c}">${categoryLabel[c]||'미분류'}</span><span class="badge ${p}">${priorityLabel[p]||'미정'}</span><span class="status ${done?'done':'pending'}">${done?'분석 완료':'분석 대기'}</span></div>
  <div class="drawer-section"><h4 class="drawer-section-title">${icon('circle')} 이슈 설명</h4><div class="detail-body">${escapeHtml(issue.body||'내용 없음')}</div></div>
  <div class="drawer-section"><h4 class="drawer-section-title">${icon('spark')} AI 분석 결과</h4>${analysisHtml}</div>`;
 const link=safeGithubUrl(issue.html_url);$('#drawerGithub').hidden=!link;$('#drawerGithub').dataset.url=link||'';
 $('#analyzeLabel').textContent=done?'다시 분석':'AI 분석 실행';
 $('#analyzeBtn').dataset.force=done?'true':'false';
}
// 실제 GitHub 이슈 분석. 쓰기 허용은 서버 환경변수에서 설정
async function runAnalysis(){
 if(state.busy||state.selected==null)return;
 const number=state.selected;state.busy=true;const btn=$('#analyzeBtn');btn.disabled=true;$('#analyzeLabel').textContent='분석 중...';
 try{
  const result=await api(`/api/issues/${number}/analyze`,{method:'POST',body:JSON.stringify({force:btn.dataset.force==='true'}),timeoutMs:120000});
  if(result.skipped){toast(result.reason,'error');}
  else{
   toast(result.applied?'분석 완료! 결과가 저장되었습니다.':'GPT 분석 완료. GitHub 쓰기가 꺼져 있어 결과는 저장하지 않았습니다.');
   if(result.notification_error)toast(result.notification_error,'error');
   await refresh(true);drawDetail(result.issue);
  }
 }catch(e){toast(e.message,'error');$('#analyzeLabel').textContent='AI 분석 실행';}finally{state.busy=false;btn.disabled=false;}
}
function registerEvents(){
 $$('[data-nav]').forEach(b=>b.addEventListener('click',()=>selectView(b.dataset.nav)));
 $$('[data-go]').forEach(b=>b.addEventListener('click',()=>selectView(b.dataset.go)));
 [$('#refreshBtn'),$('#issuesRefresh')].forEach(b=>b.addEventListener('click',()=>refresh()));
 [$('#heroCreate'),$('#newIssueBtn')].forEach(b=>b.addEventListener('click',openGithubNewIssue));
 $('#mobileMenu').addEventListener('click',()=>$('#sidebar').classList.toggle('mobile-open'));
 $('#issueSearch').addEventListener('input',renderIssues);$('#issueFilter').addEventListener('change',renderIssues);
 $('#closeDrawer').addEventListener('click',closeDrawer);$('#drawerOverlay').addEventListener('click',closeDrawer);$('#analyzeBtn').addEventListener('click',runAnalysis);
 $('#drawerGithub').addEventListener('click',()=>{const url=$('#drawerGithub').dataset.url;if(url)window.open(url,'_blank','noopener,noreferrer');});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});
}
function start(){
 registerEvents();$('#todayDate').textContent=new Intl.DateTimeFormat('ko-KR',{year:'numeric',month:'long',day:'numeric'}).format(new Date());
 const hash=(location.hash||'').slice(1);selectView(['dashboard','issues','workflow','settings'].includes(hash)?hash:'dashboard');
 refresh(true);
}
document.addEventListener('DOMContentLoaded',start);
