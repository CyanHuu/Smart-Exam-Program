const $ = (id) => document.getElementById(id);
let datasetId = '', scheduleId = localStorage.getItem('smartExamScheduleId') || '', result = JSON.parse(localStorage.getItem('smartExamResult') || '{}');
let role = '', teacherId = '', workloadItems = JSON.parse(localStorage.getItem('smartExamWorkload') || '[]');
let aiConversation = JSON.parse(localStorage.getItem('smartExamAiConversation') || '[]');
let dispatchHistory = JSON.parse(localStorage.getItem('smartExamDispatchHistory') || '[]');
function pageLabel(page) { return page === 'results' && role === 'teacher' ? '我的监考安排' : ({workbench:'排考工作台', results:'排考结果', timeline:'我的排考时间轴', workload:'查看教师工作量', dispatch:'智能调度', exports:'数据导出 / 分组导出'}[page] || '排考工作台'); }
function updatePageHeading(page) { const label = pageLabel(page); $('pageBreadcrumb').textContent = `智能排考系统 / ${label}`; $('pageTitle').textContent = label; }
const showPage = (page) => {
  document.querySelectorAll('.page-section').forEach(x => x.classList.remove('active'));
  document.querySelectorAll(`[data-page-section="${page}"]`).forEach(x => x.classList.add('active'));
  if (page === 'workbench') $('workbench').classList.add('active');
  if (page === 'workload') loadWorkload();
  updatePageHeading(page);
  if (role === 'teacher') $('adminSessionPanel').style.setProperty('display', 'none', 'important');
  document.querySelectorAll('.nav-item').forEach(x => x.classList.toggle('active', x.dataset.page === page));
  window.scrollTo({top: 0, behavior: 'smooth'});
};
document.querySelectorAll('[data-page]').forEach(item => item.addEventListener('click', event => { event.preventDefault(); showPage(item.dataset.page); history.replaceState(null, '', `#${item.dataset.page}`); }));
async function login(event) {
  event.preventDefault(); $('loginMessage').textContent = '登录中...';
  try {
    const response = await fetch('/api/v1/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:$('loginUsername').value, password:$('loginPassword').value})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '登录失败');
    role = data.role; teacherId = data.teacher_id || '';
    if (role === 'admin') {
      if (data.schedule_id !== scheduleId) {
        scheduleId = data.schedule_id || '';
        result = {};
        workloadItems = [];
        ['smartExamScheduleId', 'smartExamResult', 'smartExamSessions', 'smartExamWorkload'].forEach(key => localStorage.removeItem(key));
      }
    } else {
      scheduleId = data.schedule_id || scheduleId;
    }
    $('loginView').style.display = 'none'; $('systemStatus').textContent = role === 'admin' ? '管理员已登录' : `监考老师：${data.teacher_name}`;
    document.body.classList.toggle('teacher-mode', role === 'teacher');
    document.querySelectorAll('.admin-only').forEach(x => x.style.display = role === 'admin' ? '' : 'none');
    document.querySelectorAll('.teacher-only').forEach(x => x.style.display = role === 'teacher' ? '' : 'none');
    $('resultsNavLabel').textContent = role === 'teacher' ? '我的监考安排' : '排考结果';
    $('teacherPersonalView').style.display = role === 'teacher' ? 'block' : 'none';
    $('adminResultsView').style.display = role === 'admin' ? 'block' : 'none';
    $('adminSessionPanel').style.display = role === 'admin' ? '' : 'none';
    updatePageHeading(role === 'admin' ? 'workbench' : 'results');
    if (role === 'teacher') {
      result = {};
      if (data.schedule_id) {
        const personalResponse = await api(`/api/v1/schedules/${data.schedule_id}/teachers/${encodeURIComponent(teacherId)}`);
        const personal = await personalResponse.json();
        result = personal.results || {};
        localStorage.setItem('smartExamScheduleId', data.schedule_id);
        localStorage.setItem('smartExamResult', JSON.stringify(result));
      }
    }
    if (role === 'teacher') { document.querySelectorAll('.teacher-filter,.timeline-filter').forEach(x => x.style.display = 'none'); }
    if (Object.keys(result).length) { renderSessionOptions(); renderTeacherOptions(); render(); renderTimeline(); }
    showPage(role === 'admin' ? 'workbench' : 'results');
  } catch (e) { $('loginMessage').textContent = e.message.includes('Failed to fetch') ? '无法连接服务器，请确认 PowerShell 中的网页服务仍在运行' : e.message; }
}
$('loginForm').addEventListener('submit', login);
$('logoutButton').addEventListener('click', () => { role = ''; teacherId = ''; document.body.classList.remove('teacher-mode'); $('loginView').style.display = 'grid'; $('loginPassword').value = ''; });
const files = [['classroomFile','classroomName'], ['teacherFile','teacherName'], ['scheduleFile','scheduleName']];
function setStep(step, state, text) { const item = document.querySelector(`.progress-step[data-step="${step}"]`); if (!item) return; item.className = `progress-step ${state}`; item.querySelector('small').textContent = text; item.querySelector('.step-marker').textContent = state === 'done' ? '✓' : step; }
const stepByInput = {classroomFile:1, teacherFile:2, scheduleFile:3};
files.forEach(([input, label]) => $(input).addEventListener('change', e => {
  $(label).textContent = e.target.files[0]?.name || '请选择 .xls / .xlsx';
  if (e.target.files[0]) setStep(stepByInput[input], 'done', '文件已选择，等待读取');
}));
function log(text, success = false) { $('logs').insertAdjacentHTML('beforeend', `<p class="${success ? 'log-success' : ''}"><time>${new Date().toLocaleTimeString()}</time>${text}</p>`); }
async function api(path, options = {}) {
  const headers = {'X-API-Key': 'dev-key', ...(options.headers || {})};
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const body = await response.json().catch(() => ({})), detail = body.detail;
    const message = Array.isArray(detail) ? detail.map(x => x.msg || x.message || JSON.stringify(x)).join('；') : (detail && typeof detail === 'object' ? (detail.message || JSON.stringify(detail)) : detail);
    throw new Error(message || `请求失败（${response.status}）`);
  }
  return response;
}
let aiPolicy = null;
let aiDispatchPlan = null;
function recordDispatchHistory(solved, instruction) {
  const items = Object.values(solved.optimised || {});
  const shortage = items.reduce((n, item) => n + (item.report?.shortage || 0), 0);
  dispatchHistory = [{time:new Date().toLocaleString('zh-CN'), instruction, sessions:items.length, shortage}, ...dispatchHistory].slice(0, 20);
  localStorage.setItem('smartExamDispatchHistory', JSON.stringify(dispatchHistory));
}
function addAiChatMessage(text, role) {
  const message = document.createElement('div');
  message.className = `ai-chat-message ${role}`;
  const avatar = document.createElement('span');
  avatar.className = 'ai-avatar';
  avatar.textContent = role === 'user' ? '我' : 'AI';
  const bubble = document.createElement('div');
  bubble.className = 'ai-bubble';
  bubble.textContent = text;
  message.append(avatar, bubble);
  $('aiChatMessages').appendChild(message);
  $('aiChatMessages').scrollTop = $('aiChatMessages').scrollHeight;
  return message;
}
function renderCurrentPolicyCards(policy) {
  const host = $('currentPolicyCards');
  if (!host) return;
  const balanceIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v16M7 20h10M4 7h16M7 7l-3 6h6L7 7Zm10 0-3 6h6l-3-6Z"/></svg>';
  const genderIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="9" r="3"/><circle cx="15" cy="15" r="3"/><path d="m11 11 2 2M16.5 5.5H20v3.5M20 5.5l-4 4M7.5 18.5H4V15M4 18.5l4-4"/></svg>';
  const cards = [
    ['✦', '经验优先', '优先安排有监考经验的教师', `权重 ${policy.experience_weight}`],
    [balanceIcon, '公平分配', '尽量均衡每位教师的任务量', `权重 ${policy.fairness_weight}`],
    [genderIcon, '男女搭配', '优先优化同一考场的人员组合', `权重 ${policy.gender_weight}`],
    ['⚖', '部门均衡', '尽量安排不同部门的教师组合', `权重 ${policy.department_weight}`],
  ];
  host.innerHTML = cards.map(([icon, title, description, weight], index) => `<div class="ai-policy-card"><span class="ai-policy-icon policy-${index + 1}">${icon}</span><div><strong>${title}</strong><p>${description}</p><em>${weight}</em></div></div>`).join('');
}
async function parseAiPolicy() {
  const instruction = $('aiInstruction').value.trim();
  const isRulesQuery = instruction === '规则';
  if (!instruction) return $('aiMessage').textContent = '请输入排考要求';
  addAiChatMessage(instruction, 'user');
  $('aiInstruction').value = '';
  if (!datasetId) {
    addAiChatMessage('请先在排考工作台导入三个 Excel 文件，我才能根据真实教师和考试场次帮您解析具体规则。', 'assistant');
    return $('aiMessage').textContent = '请先导入排考数据';
  }
  $('aiParseButton').disabled = true;
  aiConversation = [...aiConversation.slice(-19), {role: 'user', content: instruction}];
  localStorage.setItem('smartExamAiConversation', JSON.stringify(aiConversation));
  $('aiMessage').textContent = '正在调用 Agnes AI 解析规则...';
  const thinkingMessage = addAiChatMessage('正在理解您的要求，请稍候', 'assistant');
  thinkingMessage.classList.add('ai-thinking');
  try {
    const data = await (await api('/api/v1/ai/parse-policy', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset_id:datasetId, schedule_id:scheduleId || null, instruction, conversation:aiConversation, current_policy:aiPolicy || {}})})).json();
    if (data.clarification_required) {
      addAiChatMessage(data.message || '请补充教师、日期或场次信息。', 'assistant');
      $('aiMessage').textContent = '请补充调度信息';
      return;
    }
    if (data.intent === 'replan') {
      aiDispatchPlan = data;
      const teacherPreview = data.teacher_ids.length > 12 ? `${data.teacher_ids.slice(0, 12).join('、')}等${data.teacher_ids.length}名教师` : data.teacher_ids.join('、');
      const previewReady = await previewAiReplan(data, teacherPreview, instruction);
      $('aiMessage').textContent = previewReady ? '已展示修改结果，请确认后执行' : '没有生成可执行的修改结果';
      return;
    }
    if ((data.intent === 'question' || data.intent === 'unsupported') && !isRulesQuery && !/排考|排班|监考/.test(instruction)) {
      addAiChatMessage(data.answer || '我只能协助处理排考规则、排考安排和教师调度问题。', 'assistant');
      $('aiMessage').textContent = '';
      return;
    }
    aiPolicy = data.policy || aiPolicy || {experience_weight:60, fairness_weight:100, gender_weight:25, department_weight:15, consecutive_gap_minutes:120, backup_count:2, max_total_count:null};
    renderCurrentPolicyCards(aiPolicy);
    $('aiPolicyPreview').textContent = JSON.stringify(aiPolicy, null, 2);
    const assistant = addAiChatMessage(isRulesQuery ? '当前排考规则如下：' : `我收到的要求是：\n“${instruction}”\n\n我理解为：请按以下规则安排监考，并在确认后生成排考方案。`, 'assistant');
    assistant.classList.add('policy-message');
    if (isRulesQuery) assistant.classList.add('rules-only');
    const bubble = assistant.querySelector('.ai-bubble');
    const constraints = document.createElement('div');
    constraints.className = 'ai-policy-constraints';
    constraints.textContent = `硬性条件：稳定性（尽量保留原安排） · 连续监考间隔 ${aiPolicy.consecutive_gap_minutes} 分钟 · 每个考场备选 ${aiPolicy.backup_count} 人${aiPolicy.max_total_count == null ? '' : ` · 总任务量不超过 ${aiPolicy.max_total_count} 次`}`;
    const confirmText = document.createElement('p');
    confirmText.className = 'ai-policy-confirm-text';
    confirmText.textContent = '以上规则是否正确？如需调整，请重新描述；确认无误后我将为您生成排考安排。';
    if (!isRulesQuery) bubble.append(constraints, confirmText);
    if (isRulesQuery) {
      const supported = document.createElement('div');
      supported.className = 'ai-supported-rules';
      supported.innerHTML = '<strong>目前支持的排考规则</strong><span>经验优先 · 公平分配 · 男女搭配 · 部门均衡</span><span>连续监考 · 教师请假 · 次数上限 · 备选监考</span>';
      bubble.append(supported);
      $('aiMessage').textContent = '';
      $('aiChatMessages').scrollTop = $('aiChatMessages').scrollHeight;
      return;
    }
    const actions = document.createElement('div');
    actions.className = 'ai-policy-actions';
    const retryButton = document.createElement('button');
    retryButton.className = 'outline-button'; retryButton.textContent = '重新描述';
    retryButton.addEventListener('click', () => {
      actions.querySelectorAll('button').forEach(button => button.disabled = true);
      addAiChatMessage('好的，我们重新描述这次排考要求。您可以直接告诉我教师、日期、考试场次、备选监考人数或其他限制条件，我会重新为您整理方案。', 'assistant');
      $('aiInstruction').placeholder = '请重新描述排考要求，例如：每个考场安排1名备选监考...';
      $('aiInstruction').focus();
      $('aiMessage').textContent = '请重新输入排考要求';
    });
    const solveButton = document.createElement('button');
    solveButton.id = 'aiSolveButton'; solveButton.className = 'primary-button'; solveButton.textContent = '确认并开始排考';
    solveButton.addEventListener('click', solveWithAiPolicy);
    actions.append(retryButton, solveButton); assistant.appendChild(actions);
    $('aiChatMessages').scrollTop = $('aiChatMessages').scrollHeight;
    $('aiMessage').textContent = '规则解析完成，请确认后开始排考';
  } catch (e) { $('aiMessage').textContent = e.message; }
  finally { thinkingMessage.remove(); $('aiParseButton').disabled = false; }
}
async function solveWithAiPolicy() {
  if (!datasetId || !aiPolicy) return;
  const button = $('aiSolveButton');
  button.disabled = true;
  button.dataset.originalText = button.textContent;
  button.textContent = '正在生成…';
  $('aiMessage').textContent = '正在校验规则并生成排考方案，请稍候…';
  try {
    const validation = await (await api('/api/v1/policies/validate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset_id:datasetId, policy:aiPolicy})})).json();
    if (!validation.valid) throw new Error(validation.errors.join('；'));
    const solvePolicy = {...validation.policy, time_limit_seconds: Math.min(validation.policy.time_limit_seconds || 20, 8)};
    const solved = await (await api('/api/v1/schedules/solve', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset_id:datasetId, policy:solvePolicy})})).json();
    scheduleId = solved.schedule_id; result = solved.optimised; workloadItems = solved.workload || [];
    recordDispatchHistory(solved, aiConversation.at(-1)?.content || '未记录要求');
    localStorage.setItem('smartExamScheduleId', scheduleId); localStorage.setItem('smartExamResult', JSON.stringify(result)); localStorage.setItem('smartExamWorkload', JSON.stringify(workloadItems));
    renderExportSessions(); $('sessionSelect').innerHTML = Object.values(result).map(x => `<option value="${x.session.session_id}">${x.session.session_id}｜${x.session.period_text || x.session.start}</option>`).join(''); render(); renderTeacherOptions(); loadWorkload(); showPage('results');
    renderDispatchTask(solved);
    addAiChatMessage(`排考完成：共${Object.keys(result).length}个场次，人员缺口${Object.values(result).reduce((n, x) => n + (x.report.shortage || 0), 0)}人。详细结果已同步到右侧任务卡。`, 'assistant');
    $('aiMessage').textContent = 'AI 排考完成'; $('systemStatus').textContent = '排考完成';
  } catch (e) { $('aiMessage').textContent = e.message; }
  finally { button.disabled = false; button.textContent = button.dataset.originalText || '确认并开始排考'; }
}
async function previewAiReplan(plan, teacherPreview, instruction) {
  try {
    addAiChatMessage(`我收到的要求是：\n“${instruction}”\n\n我理解为：以下教师在指定场次不可参加监考，正在生成替换方案。`, 'assistant');
    const unavailableBySession = Object.fromEntries(plan.affected_session_ids.map(sessionId => [sessionId, plan.teacher_ids]));
    const preview = await (await api('/api/v1/schedules/replan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({schedule_id:scheduleId, unavailable_teacher_ids:[], unavailable_by_session:unavailableBySession, affected_session_ids:plan.affected_session_ids, policy:aiPolicy || {}, preview:true})})).json();
    const changes = (preview.changes || []).slice(0, 12).map(change => `${change.session_id} / ${change.room}：${change.removed.map(x => x.name).join('、') || '无'} → ${change.added.map(x => x.name).join('、') || '无'}`);
    if (!changes.length) {
      addAiChatMessage('当前调度不会产生人员替换，请检查教师、日期或场次描述。暂不提供确认按钮。', 'assistant');
      return false;
    }
    const assistant = addAiChatMessage(`好的，我先展示本次修改结果：\n\n批量教师：${teacherPreview}\n影响场次：${plan.affected_session_ids.join('、')}\n修改考场：${preview.changed_rooms || changes.length} 个\n\n${changes.join('\n')}${(preview.changes || []).length > changes.length ? `\n……另有 ${(preview.changes || []).length - changes.length} 个考场变更` : ''}\n\n以上仅为预览，确认后才会更新排考表；未涉及的场次保持不变。`, 'assistant');
    const replanButton = document.createElement('button');
    replanButton.id = 'aiReplanButton';
    replanButton.className = 'primary-button ai-inline-solve-button';
    replanButton.textContent = '同意并更新排考表';
    replanButton.addEventListener('click', confirmAiReplan);
    assistant.querySelector('.ai-bubble').appendChild(replanButton);
    return true;
  } catch (e) {
    addAiChatMessage(`暂时无法生成修改预览：${e.message}。暂不提供确认按钮，请修改描述后重试。`, 'assistant');
    return false;
  }
}
async function confirmAiReplan() {
  if (!scheduleId || !aiDispatchPlan) return;
  const button = $('aiReplanButton');
  if (button) button.disabled = true;
  $('aiMessage').textContent = '正在按确认的时间范围重新调度...';
  try {
    const unavailableBySession = Object.fromEntries(aiDispatchPlan.affected_session_ids.map(sessionId => [sessionId, aiDispatchPlan.teacher_ids]));
    const replanned = await (await api('/api/v1/schedules/replan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({schedule_id:scheduleId, unavailable_teacher_ids:[], unavailable_by_session:unavailableBySession, affected_session_ids:aiDispatchPlan.affected_session_ids, policy:aiPolicy || {}})})).json();
    scheduleId = replanned.schedule_id; result = replanned.optimised; workloadItems = replanned.workload || []; aiDispatchPlan = null;
    recordDispatchHistory(replanned, aiConversation.at(-1)?.content || '教师调度调整');
    localStorage.setItem('smartExamScheduleId', scheduleId); localStorage.setItem('smartExamResult', JSON.stringify(result)); localStorage.setItem('smartExamWorkload', JSON.stringify(workloadItems));
    renderExportSessions(); renderSessionOptions(); render(); renderTeacherOptions(); renderDispatchTask(replanned); loadWorkload();
    addAiChatMessage(`调度已完成，已更新${replanned.changed_rooms || 0}个考场。其他未受影响场次保持不变。`, 'assistant');
    $('aiMessage').textContent = '教师调度完成'; $('systemStatus').textContent = '调度完成';
  } catch (e) { $('aiMessage').textContent = e.message; }
  finally { if (button) button.disabled = false; }
}
function renderDispatchTask(solved) {
  const comparison = solved.comparison || {};
  const items = Object.values(solved.optimised || {});
  const shortage = items.reduce((n, x) => n + (x.report.shortage || 0), 0);
  const backup = items.reduce((n, x) => n + (x.report.backup_total || 0), 0);
  const rooms = items.reduce((n, x) => n + (x.report.total_rooms || 0), 0);
  $('dispatchTaskStatus').textContent = shortage ? '存在缺口' : '排考完成';
  $('dispatchTaskStatus').classList.toggle('task-warning', Boolean(shortage));
  $('dispatchTaskSummary').innerHTML = `<div class="dispatch-task-result"><div class="dispatch-task-result-head"><strong>本次排考任务</strong><span>${new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'})}</span></div><div class="dispatch-task-kpis"><div><small>考试场次</small><strong>${items.length}</strong></div><div><small>考场数量</small><strong>${rooms}</strong></div><div class="${shortage ? 'warning' : ''}"><small>人员缺口</small><strong>${shortage} 人</strong></div><div><small>备选监考</small><strong>${backup} 人</strong></div></div><div class="dispatch-task-detail"><p><span>执行状态</span><b>${shortage ? '已完成，但存在人员缺口' : '已完成，规则校验通过'}</b></p><p><span>公平性优化</span><b>${comparison.fairness || comparison.fairness_change || '已纳入均衡分配'}</b></p><p><span>下一步</span><b>前往“排考结果”查看人员、考场和时间轴</b></p></div></div>`;
}
if ($('aiParseButton')) { $('aiParseButton').textContent = '➤'; $('aiParseButton').setAttribute('aria-label', '发送消息'); $('aiParseButton').title = '发送消息'; }
$('aiParseButton')?.addEventListener('click', parseAiPolicy);
$('aiInstruction')?.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    parseAiPolicy();
  }
});
$('dispatchHistoryButton')?.addEventListener('click', () => {
  if (!dispatchHistory.length) return addAiChatMessage('当前暂无调度历史。确认一次排考后，这里会记录方案摘要。', 'assistant');
  const text = dispatchHistory.slice(0, 8).map((item, index) => `${index + 1}. ${item.time}｜${item.instruction}\n   ${item.sessions} 个场次，人员缺口 ${item.shortage} 人`).join('\n');
  addAiChatMessage(`调度历史（最近 ${Math.min(dispatchHistory.length, 8)} 次）:\n\n${text}`, 'assistant');
});
$('aiSolveButton')?.addEventListener('click', solveWithAiPolicy);
function current() { return result[$('sessionSelect').value]; }
function renderSessionOptions() { const sessions = Object.values(result); $('sessionSelect').innerHTML = sessions.map(x => `<option value="${x.session.session_id}">${x.session.session_id}｜${x.session.period_text || x.session.start}</option>`).join('') || '<option value="">请先完成排考</option>'; }
function teacherList() { const map = new Map(); Object.values(result).forEach(item => [...Object.values(item.assignments).flat(), ...Object.values(item.backups || {}).flat()].forEach(t => map.set(t.teacher_id, t.name))); return [...map].map(([id, name]) => ({id, name})); }
function renderTeacherOptions() { const teachers = teacherList().filter(t => role !== 'teacher' || t.id === teacherId), selects = [$('teacherSelect'), $('timelineTeacherSelect')].filter(Boolean); selects.forEach(select => { const old = teacherId || select.value; select.innerHTML = teachers.map(t => `<option value="${t.id}">${t.name}（${t.id}）</option>`).join('') || '<option value="">暂无教师数据</option>'; if (teachers.some(t => t.id === old)) select.value = old; }); }
function renderTimeline() { const teacherId = $('timelineTeacherSelect').value; if (!teacherId) { $('timelineBody').innerHTML = '<div class="timeline-empty">请选择监考老师查看个人排考时间表</div>'; return; } const tasks = []; Object.values(result).forEach(item => { Object.entries(item.assignments).forEach(([room, teachers]) => teachers.forEach((t, i) => { if (t.teacher_id === teacherId) tasks.push({item, room, role: i ? '正式监考' : '第一监考', color: i ? 'blue' : 'green'}); })); Object.entries(item.backups || {}).forEach(([room, teachers]) => teachers.forEach(t => { if (t.teacher_id === teacherId) tasks.push({item, room, role:'备选监考', color:'orange'}); })); }); tasks.sort((a,b) => new Date(a.item.session.start) - new Date(b.item.session.start)); $('timelineBody').innerHTML = tasks.map(x => `<div class="timeline-card ${x.color}"><div class="timeline-time">${x.item.session.period_text || x.item.session.start}</div><div class="timeline-main"><strong>${x.item.session.session_id} · ${x.room}</strong><span>${x.item.session.title || '考试场次'} · ${x.role}</span></div></div>`).join('') || '<div class="timeline-empty">该教师暂无监考安排</div>'; }
function renderTimeline() { const teacherId = $('timelineTeacherSelect').value; if (!teacherId) { $('timelineBody').innerHTML = '<div class="timeline-empty">请选择监考老师查看个人排考时间表</div>'; return; } const tasks = []; Object.values(result).forEach(item => { Object.entries(item.assignments).forEach(([room, teachers]) => teachers.forEach((t, i) => { if (t.teacher_id === teacherId) tasks.push({item, room, role: i ? '正式监考' : '第一监考', color: i ? 'blue' : 'green'}); })); Object.entries(item.backups || {}).forEach(([room, teachers]) => teachers.forEach(t => { if (t.teacher_id === teacherId) tasks.push({item, room, role:'备选监考', color:'orange'}); })); }); tasks.sort((a,b) => new Date(a.item.session.start) - new Date(b.item.session.start)); if (!tasks.length) { $('timelineBody').innerHTML = '<div class="timeline-empty">该教师暂无监考安排</div>'; return; } const dates = new Map(); tasks.forEach(task => { const start = new Date(task.item.session.start), end = new Date(task.item.session.end), date = start.toLocaleDateString('zh-CN'), startHour = start.getHours() + start.getMinutes() / 60, endHour = end.getHours() + end.getMinutes() / 60; if (!dates.has(date)) dates.set(date, []); dates.get(date).push({...task, startHour, endHour}); }); const times = Array.from({length: 11}, (_, i) => 8 + i); $('timelineBody').innerHTML = `<div class="timeline-grid"><div class="timeline-header"><strong>日期</strong><div class="time-scale">${times.map(h => `<span>${String(h).padStart(2,'0')}:00</span>`).join('')}</div></div>${[...dates].map(([date, dateTasks]) => `<div class="timeline-row"><div class="timeline-date">${date}</div><div class="timeline-track">${dateTasks.map(x => { const left = Math.max(0, Math.min(100, (x.startHour - 8) / 10 * 100)), width = Math.max(12, Math.min(100 - left, (x.endHour - x.startHour) / 10 * 100)), session = x.startHour < 12 ? '上午场' : '下午场', startText = `${String(Math.floor(x.startHour)).padStart(2,'0')}:${String(Math.round((x.startHour % 1) * 60)).padStart(2,'0')}`, endText = `${String(Math.floor(x.endHour)).padStart(2,'0')}:${String(Math.round((x.endHour % 1) * 60)).padStart(2,'0')}`; return `<div class="timeline-task ${x.color}" style="left:${left}%;width:${width}%"><strong>${session} · ${x.room}</strong><span>${x.item.session.title || '考试科目'}</span><small>${startText}-${endText} · ${x.role}</small></div>`; }).join('')}</div></div>`).join('')}</div>`; }
function teacherTasks() { const tasks = []; Object.values(result).forEach(item => { Object.entries(item.assignments || {}).forEach(([room, teachers]) => teachers.forEach((teacher, index) => { if (!teacherId || teacher.teacher_id === teacherId) tasks.push({item, room, teacher, role: index ? '监考人员' : '第一监考', color: index ? 'formal' : 'first'}); })); Object.entries(item.backups || {}).forEach(([room, teachers]) => teachers.forEach(teacher => { if (!teacherId || teacher.teacher_id === teacherId) tasks.push({item, room, teacher, role: '备选监考', color: 'backup'}); })); }); return tasks.sort((a, b) => new Date(a.item.session.start) - new Date(b.item.session.start)); }
function renderTeacherHome() { const tasks = teacherTasks(), next = tasks[0], title = $('teacherNextTitle'), details = $('teacherNextDetails'); if (!next) { title.textContent = '当前暂无监考安排'; details.innerHTML = '<div class="teacher-empty">排考方案中暂未找到你的监考任务</div>'; $('teacherNextStatus').textContent = '暂无安排'; $('teacherUpcomingList').innerHTML = '<div class="teacher-empty">暂无近期监考安排</div>'; return; } const session = next.item.session, start = new Date(session.start), end = new Date(session.end), period = start.getHours() < 12 ? '上午场' : '下午场'; title.textContent = session.title || '考试科目'; $('teacherNextStatus').textContent = next.role; details.innerHTML = `<div><small>日期</small><strong>${start.toLocaleDateString('zh-CN')}</strong></div><div><small>考试时间</small><strong>${period}　${start.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}—${end.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}</strong></div><div><small>考场</small><strong>${next.room}</strong></div><div><small>监考角色</small><strong>${next.role}</strong></div>`; $('teacherUpcomingList').innerHTML = tasks.map(task => { const s = new Date(task.item.session.start), e = new Date(task.item.session.end), p = s.getHours() < 12 ? '上午场' : '下午场'; return `<div class="teacher-assignment-row"><div class="assignment-date"><strong>${s.getMonth()+1}月${s.getDate()}日</strong><small>${s.toLocaleDateString('zh-CN',{weekday:'short'})}</small></div><div><strong>${p}　${task.item.session.title || '考试科目'}</strong><small>${s.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}—${e.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}　·　${task.room}</small></div><span class="role-badge ${task.color}">${task.role}</span></div>`; }).join(''); }
function renderTimeline() { const tasks = teacherTasks(); if (!tasks.length) { $('timelineBody').innerHTML = '<div class="timeline-empty">当前登录教师暂无监考安排</div>'; return; } const dates = new Map(); tasks.forEach(task => { const start = new Date(task.item.session.start), end = new Date(task.item.session.end), date = start.toLocaleDateString('zh-CN'), startHour = start.getHours() + start.getMinutes() / 60, endHour = end.getHours() + end.getMinutes() / 60; if (!dates.has(date)) dates.set(date, []); dates.get(date).push({...task, startHour, endHour}); }); const times = Array.from({length: 11}, (_, i) => 8 + i); $('timelineBody').innerHTML = `<div class="timeline-grid"><div class="timeline-header"><strong>日期</strong><div class="time-scale">${times.map(h => `<span>${String(h).padStart(2,'0')}:00</span>`).join('')}</div></div>${[...dates].map(([date, dateTasks]) => `<div class="timeline-row"><div class="timeline-date">${date}</div><div class="timeline-track">${dateTasks.map(x => { const left = Math.max(0, Math.min(100, (x.startHour - 8) / 10 * 100)), width = Math.max(12, Math.min(100 - left, (x.endHour - x.startHour) / 10 * 100)), session = x.startHour < 12 ? '上午场' : '下午场', startText = `${String(Math.floor(x.startHour)).padStart(2,'0')}:${String(Math.round((x.startHour % 1) * 60)).padStart(2,'0')}`, endText = `${String(Math.floor(x.endHour)).padStart(2,'0')}:${String(Math.round((x.endHour % 1) * 60)).padStart(2,'0')}`; return `<div class="timeline-task ${x.color}" style="left:${left}%;width:${width}%"><strong>${session} · ${x.room}</strong><span>${x.item.session.title || '考试科目'}</span><small>${startText}-${endText} · ${x.role}</small></div>`; }).join('')}</div></div>`).join('')}</div>`; }
function render() {
  if (role === 'teacher') { renderTeacherHome(); return; }
  const item = current(); if (!item) return;
  $('sessionTag').textContent = `场次 ${item.session.session_id}`;
  const roomNames = Object.keys(item.assignments), previousRoom = $('roomSelect').value, selectedRoom = previousRoom === '__all__' || roomNames.includes(previousRoom) ? previousRoom : '__all__';
  $('roomSelect').innerHTML = `<option value="__all__">全部考场</option>${roomNames.map(room => `<option value="${room}">${room}</option>`).join('')}`; $('roomSelect').value = selectedRoom;
  const report = item.report, rows = [];
  $('roomTotal').textContent = report.total_rooms; $('neededTotal').textContent = report.total_needed; $('assignedTotal').textContent = report.total_assigned; $('shortageTotal').textContent = report.shortage;
  const backupShortage = report.backup_shortage || 0; $('backupNotice').hidden = !backupShortage; $('backupNotice').textContent = backupShortage ? `备选监考人员不足：还缺 ${backupShortage} 个备选名额。当前场次有 ${report.backup_total || 0} 名备选人员，建议补充教师或降低备选人数要求。` : '';
  const roomsToRender = selectedRoom === '__all__' ? roomNames : [selectedRoom], teacherId = role === 'teacher' ? $('teacherSelect').value : '';
  roomsToRender.forEach(room => { (item.assignments[room] || []).forEach((teacher, i) => { if (!teacherId || teacher.teacher_id === teacherId) rows.push(`<tr><td>${room}</td><td>${teacher.name}</td><td>${teacher.teacher_id}</td><td>${teacher.department}</td><td>${teacher.gender}</td><td>${teacher.experienced ? '有' : '无'}</td><td>${i ? '监考人员' : '第一监考'}</td><td class="${(item.backups[room] || []).length ? '' : 'no-backup'}">${(item.backups[room] || []).map(x => x.name).join('、') || '暂无备选'}</td></tr>`); }); if (teacherId) (item.backups[room] || []).forEach(teacher => { if (teacher.teacher_id === teacherId) rows.push(`<tr><td>${room}</td><td>${teacher.name}</td><td>${teacher.teacher_id}</td><td>${teacher.department}</td><td>${teacher.gender}</td><td>${teacher.experienced ? '有' : '无'}</td><td>备选监考</td><td>—</td></tr>`); }); const shortage = (report.unfilled_rooms || []).find(x => x.room === room); if (shortage) rows.push(`<tr class="warning shortage-row"><td>${room}</td><td colspan="7"><strong>人员不足</strong>：已安排 ${shortage.assigned}/${shortage.needed} 人，缺少 ${shortage.missing} 名监考教师</td></tr>`); });
  if (!rows.length) rows.push(`<tr class="warning"><td colspan="8">${teacherId ? '该教师在此时段没有安排' : '此时段没有安排'}</td></tr>`);
  $('resultBody').innerHTML = rows.join('');
  $('summary').innerHTML = `<span class="success">✓ 已安排 ${report.total_assigned} 人</span><i></i><span>缺口 ${report.shortage} 人</span><i></i><span>备选 ${report.backup_total || 0} 人</span>`;
  $('timelineBody').innerHTML = Object.values(result).map(x => `<div class="timeline-item"><strong>${x.session.session_id}</strong>　${x.session.period_text || x.session.start}　·　${x.report.total_rooms} 个考场　·　已安排 ${x.report.total_assigned} 人</div>`).join('');
}
function renderExportSessions() { const sessions = Object.values(result); if (!sessions.length) { try { const saved = JSON.parse(localStorage.getItem('smartExamSessions') || '[]'); $('exportSession').innerHTML = saved.length ? `<option value="__all__">全部日期</option>${saved.map(x => `<option value="${x.id}">${x.label}</option>`).join('')}` : '<option value="">请先完成排考</option>'; } catch (_) {} return; } $('exportSession').innerHTML = `<option value="__all__">全部日期</option>${sessions.map(x => `<option value="${x.session.session_id}">${x.session.session_id}｜${x.session.period_text || x.session.start}</option>`).join('')}`; }
$('sessionSelect').addEventListener('change', render);
$('roomSelect').addEventListener('change', render);
$('teacherSelect').addEventListener('change', render);
$('solveButton').addEventListener('click', async () => {
  const classroom = $('classroomFile').files[0], teacher = $('teacherFile').files[0], schedule = $('scheduleFile').files[0];
  if (!classroom || !teacher || !schedule) return $('message').textContent = '请先选择三个 Excel 文件';
  $('solveButton').disabled = true; $('message').textContent = '正在读取并排考...'; $('logs').innerHTML = ''; setStep(4, 'active', '正在智能排考，请稍候');
  try {
    const body = new FormData(); body.append('classroom_file', classroom); body.append('teacher_file', teacher); body.append('schedule_file', schedule);
    const imported = await (await api('/api/v1/datasets/import', {method:'POST', body})).json(); datasetId = imported.dataset_id; aiPolicy = null; aiDispatchPlan = null; aiConversation = []; localStorage.removeItem('smartExamAiConversation'); $('aiPolicyPreview').textContent = ''; [1,2,3].forEach(x => setStep(x, 'done', '读取成功')); log(`读取完成：${imported.summary.teachers} 名教师、${imported.summary.sessions} 个场次`);
    const solved = await (await api('/api/v1/schedules/solve', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({dataset_id: datasetId, policy:{}})})).json();
    scheduleId = solved.schedule_id; localStorage.setItem('smartExamScheduleId', scheduleId); result = solved.optimised; localStorage.setItem('smartExamSessions', JSON.stringify(Object.values(result).map(x => ({id:x.session.session_id, label:`${x.session.session_id}｜${x.session.period_text || x.session.start}`})))); renderExportSessions(); setStep(4, 'done', '排考完成'); $('sessionSelect').innerHTML = Object.values(result).map(x => `<option value="${x.session.session_id}">${x.session.session_id}｜${x.session.period_text || x.session.start}</option>`).join(''); render(); showPage('results'); log('智能排考完成，结果校验通过', true); $('systemStatus').textContent = '排考完成'; $('message').textContent = '排考成功';
    workloadItems = solved.workload || [];
    localStorage.setItem('smartExamWorkload', JSON.stringify(workloadItems));
    localStorage.setItem('smartExamResult', JSON.stringify(result));
    renderTeacherOptions();
  } catch (e) { $('message').textContent = e.message; log(`错误：${e.message}`); } finally { $('solveButton').disabled = false; }
});
async function exportSchedule() { if (!scheduleId) return; try { const response = await api('/api/v1/schedules/export', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({schedule_id:scheduleId})}); const blob = await response.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] || '智能排考结果.xlsx'; a.click(); } catch (e) { $('message').textContent = e.message; } }
$('exportButton2').addEventListener('click', exportSchedule);
async function exportGroups() { if (!scheduleId) return $('exportMessage').textContent = '请先完成一次智能排考'; const rules = $('groupRules').value.trim(), sessionId = $('exportSession').value; if (!sessionId) return $('exportMessage').textContent = '请先选择当前考试场次'; if (!rules) return $('exportMessage').textContent = '请先填写考场分组规则'; $('groupExportButton').disabled = true; $('exportMessage').textContent = '正在生成分组文件...'; try { const response = await api('/api/v1/schedules/export-groups', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({schedule_id:scheduleId, session_id:sessionId, rules})}); const blob = await response.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] || `智能排考_${sessionId}_分组结果.xlsx`; a.click(); $('exportMessage').textContent = '分组文件已生成'; } catch (e) { $('exportMessage').textContent = e.message; } finally { $('groupExportButton').disabled = false; } }
$('groupExportButton').addEventListener('click', exportGroups);
renderExportSessions();
function renderWorkload() { const keyword = $('workloadSearch').value.trim().toLowerCase(); const sort = $('workloadSort').value; const items = workloadItems.filter(x => `${x.teacher_id}${x.name}`.toLowerCase().includes(keyword)).sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name, 'zh-CN') : b[`${sort}_count`] - a[`${sort}_count`]); const formal = items.reduce((n, x) => n + x.formal_count, 0), backup = items.reduce((n, x) => n + x.backup_count, 0), total = formal + backup, maxTotal = Math.max(1, ...items.map(x => x.total_count)); $('teacherTotal').textContent = items.length; $('formalTotal').textContent = formal; $('backupTotal').textContent = backup; $('averageTotal').textContent = items.length ? (total / items.length).toFixed(1) : '0'; $('workloadSummary').textContent = `当前显示 ${items.length} 名教师 · 共 ${total} 项任务`; $('workloadBody').innerHTML = items.map(x => `<tr><td>${x.teacher_id}</td><td><strong>${x.name}</strong></td><td><span class="count formal">${x.formal_count}</span></td><td><span class="count backup">${x.backup_count}</span></td><td><strong>${x.total_count}</strong></td><td><span class="workload-ratio"><span class="workload-bar"><i style="width:${Math.round(x.total_count / maxTotal * 100)}%"></i></span><em>${x.total_count}/${maxTotal}</em></span></td><td class="workload-detail">${x.sessions?.map(s => `${s.session} / ${s.room}（${s.role}）`).join('<br>') || '—'}</td></tr>`).join('') || '<tr><td colspan="7">没有匹配的教师</td></tr>'; }
async function loadWorkload() { if (!scheduleId && !workloadItems.length) { $('workloadSummary').textContent = '请先完成一次智能排考'; $('workloadBody').innerHTML = '<tr><td colspan="7">暂无排考方案，请先在排考工作台完成排考</td></tr>'; return; } if (workloadItems.length) renderWorkload(); if (!scheduleId) return; try { const data = await (await api(`/api/v1/schedules/${scheduleId}/workload`)).json(); workloadItems = data.items; localStorage.setItem('smartExamWorkload', JSON.stringify(workloadItems)); renderWorkload(); } catch (e) { if (!workloadItems.length) { $('workloadSummary').textContent = e.message; $('workloadBody').innerHTML = `<tr><td colspan="7">无法加载教师工作量：${e.message}</td></tr>`; } } }
$('workloadSearch').addEventListener('input', renderWorkload);
$('workloadSort').addEventListener('change', renderWorkload);
renderCurrentPolicyCards(aiPolicy || {experience_weight:60, fairness_weight:100, gender_weight:25, department_weight:15});
