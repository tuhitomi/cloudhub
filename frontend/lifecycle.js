// Lifecycle decisions and their cost/data consent; all authorization stays at the backend.
export const operatingPolicy=env=>env.operating_policy||{mode:'AUTO_OFF',period_minutes:Number(env.duration_hours||1)*60,ending_policy:'BACKUP_RETIRE',budget_limit_milliusd:null,source:'legacy_no_auto_renew'};
export const usd=value=>value==null?'Chưa xác định':(Number(value)/1000).toFixed(3)+' USD';
export const modeName=value=>value==='AUTO_RENEW'?'Tự động gia hạn':'Tự động tắt';
export const endingName=value=>value==='PAUSE'?'Tạm dừng, giữ dữ liệu':'Sao lưu rồi thu hồi';

export function createLifecycleUI({api,mutate,track,refresh,element,timestamp,notice}){
  const $=s=>document.querySelector(s);let createTemplate,managed,action,currentQuote,serial=0;
  const canPause=env=>['T01','T05'].includes(env.template_id);
  function endings(select,env,selected='BACKUP_RETIRE'){
    select.replaceChildren();for(const value of canPause(env)?['BACKUP_RETIRE','PAUSE']:['BACKUP_RETIRE']){const option=element('option',endingName(value));option.value=value;select.append(option);}select.value=selected;
  }
  function policy(mode,period,ending,budget){return {mode,period_minutes:Number(period),ending_policy:ending,budget_limit_milliusd:mode==='AUTO_RENEW'?Math.round(Number(budget)*1000):null,confirmed:true};}
  function configureCreate(template){
    createTemplate=template;$('#operating-mode').value='AUTO_OFF';$('#operating-period').value='60';$('#operating-budget').value='10';
    endings($('#ending-policy'),template);$('#pause-support').textContent=canPause(template)?'Tạm dừng giữ máy và volume; EBS còn phát sinh phí.':'Mẫu này không hỗ trợ tạm dừng. Chỉ sao lưu rồi thu hồi.';
    toggleCreate();
  }
  function toggleCreate(){const renewing=$('#operating-mode').value==='AUTO_RENEW';$('#operating-budget-field').hidden=!renewing;$('#operating-budget').required=renewing;
    $('#operating-explanation').textContent=renewing?'Bạn chủ động bật tự động gia hạn. Mỗi kỳ kiểm lại trạng thái, hạn mức và dự toán cộng dồn; nếu không đủ điều kiện sẽ áp dụng chính sách kết thúc đã chọn.':'Đến mốc xử lý, hệ thống áp dụng chính sách kết thúc đã chọn. Khoảng xem dự toán không tự quyết định mốc này.';
  }
  function createRequest(){return {template_id:createTemplate.template_id,estimate_hours:Number($('#hours').value),operating_policy:policy($('#operating-mode').value,$('#operating-period').value,$('#ending-policy').value,$('#operating-budget').value)};}
  function renderQuote(data){
    const host=$('#quote');host.replaceChildren();
    if(!data.first_period){host.append(element('p','Chưa nhận được dự toán theo chính sách vận hành.'));return;}
    const rows=[['Khoảng xem dự toán '+data.estimate_hours+' giờ (không đặt lịch tắt)',data.preview.total_milliusd],
      ['Kỳ vận hành đầu '+data.operating_policy.period_minutes+' phút',data.first_period.total_milliusd],
      ['Mỗi kỳ gia hạn '+data.operating_policy.period_minutes+' phút',data.renewal_period.total_milliusd],
      ['Giữ dữ liệu sau kết thúc trong '+data.after_shutdown.period_hours+' giờ',data.after_shutdown.total_milliusd],
      ['Dự toán cam kết ban đầu, gồm dự phòng giữ dữ liệu',data.initial_commitment_milliusd]];
    for(const [label,value] of rows)host.append(element('p',label+': '+usd(value)));
    host.append(element('p',data.budget_scope),element('p','Giới hạn dự toán phục vụ quyết định gia hạn, không bảo đảm hóa đơn AWS thực tế.'),
      element('small','Đơn giá cập nhật: '+(data.first_period.price_updated_at||'Chưa xác nhận')+' · Region: '+(data.first_period.region||'Chưa xác nhận')));
    if(data.budget_feasible===false)host.append(element('p','Giới hạn cộng dồn chưa đủ kỳ đầu hoặc vượt ngân sách hệ thống.','warning'));
    host.append(element('p',[...(data.unknown||[]),...(data.after_shutdown.unknown||[])].join(' ')));
    const details=element('details');details.append(element('summary','Đơn giá, giả định và các khoản dự toán'),element('pre',JSON.stringify({first:data.first_period,renewal:data.renewal_period,after_shutdown:data.after_shutdown},null,2)));host.append(details);
  }
  function field(id,label,type,value){const wrapper=element('label',label),input=element('input');input.id=id;input.type=type;input.required=true;input.value=value;wrapper.append(input);return wrapper;}
  function select(id,label,values,value){const wrapper=element('label',label),input=element('select');input.id=id;for(const [key,text] of values){const option=element('option',text);option.value=key;input.append(option);}input.value=value;wrapper.append(input);return wrapper;}
  const names={renew:'Gia hạn thủ công',change_policy:'Đổi chế độ vận hành',resume_paused:'Mở lại môi trường tạm dừng',end:'Kết thúc theo chính sách',destroy:'Sao lưu rồi thu hồi',backup:'Tạo bản sao thủ công'};
  function request(){
    const body={action};
    if(action==='renew')body.minutes=Number($('#management-minutes').value);
    if(action==='change_policy')body.operating_policy=policy($('#management-mode').value,$('#management-period').value,$('#management-ending').value,$('#management-budget').value);
    return body;
  }
  async function updateManagementQuote(){
    const run=++serial;currentQuote=null;$('#management-confirmed').checked=false;$('#management-submit').disabled=true;
    if(action==='change_policy'){const renewing=$('#management-mode').value==='AUTO_RENEW';const budget=$('#management-budget');budget.required=renewing;budget.disabled=!renewing;budget.parentElement.hidden=!renewing;}
    try{
      const quote=await api('/environments/'+managed.environment_id+'/management-estimate',{method:'POST',body:JSON.stringify(request())});
      if(run!==serial)return;currentQuote=quote;$('#management-quote').replaceChildren(element('p','Dự toán bổ sung: '+usd(quote.additional_commitment_milliusd)),element('p','Cộng dồn sau tác vụ: '+usd(quote.cumulative_after_milliusd)),
        element('p','Giữ dữ liệu sau kết thúc: '+usd(quote.after_shutdown.total_milliusd)+' / '+quote.after_shutdown.period_hours+' giờ'),element('p',(quote.unknown||[]).join(' ')),element('p','Dự toán không phải giới hạn hóa đơn AWS.'));
      const details=element('details');details.append(element('summary','Đơn giá và giả định'),element('pre',JSON.stringify(quote,null,2)));$('#management-quote').append(details);$('#management-submit').disabled=!quote.approved;$('#management-notice').textContent='';
    }catch(error){if(run===serial)$('#management-notice').textContent=error.message;}
  }
  async function openManagement(env,nextAction){
    managed=await api('/environments/'+env.environment_id);action=nextAction;const p=operatingPolicy(managed);
    $('#management-title').textContent=names[action];$('#management-fields').replaceChildren();$('#management-notice').textContent='';$('#management-quote').replaceChildren();
    const explanations={renew:'Giữ nguyên tài nguyên và dữ liệu. Chỉ tăng deadline, dự toán cộng dồn và đồng bộ lịch. Không dựng lại môi trường.',
      change_policy:'Mốc xử lý hiện tại được giữ nguyên; chu kỳ mới áp dụng cho kỳ tiếp theo. Ngân sách cộng dồn không đặt lại. Thay chính sách giữ dữ liệu có thể cần thêm dự phòng chi phí.',
      resume_paused:'Khởi động lại chính máy hiện có, giữ volume và dữ liệu. Địa chỉ IP có thể đổi; phát sinh dự toán kỳ mới.',
      end:'Áp dụng '+endingName(p.ending_policy)+'. Tạm dừng vẫn giữ hạ tầng và phí lưu dữ liệu; thu hồi xóa hạ tầng sau khi sao lưu thành công.',
      destroy:'Sao lưu dữ liệu theo phạm vi của mẫu rồi thu hồi hạ tầng. Dữ liệu ngoài phạm vi sao lưu không được bảo vệ. Máy tạm dừng có thể phải khởi động để sao lưu; bản sao/log vẫn có phí.',
      backup:'Tạo thêm bản sao và dự toán bổ sung. Một số mẫu tạm dừng ghi hoặc máy trong lúc sao lưu, sau đó hoạt động trở lại.'};
    $('#management-description').textContent=explanations[action];
    if(action==='renew'){const f=field('management-minutes','Thêm số phút vận hành','number',p.period_minutes);f.querySelector('input').min=5;f.querySelector('input').max=480;f.querySelector('input').step=1;$('#management-fields').append(f);}
    if(action==='change_policy'){
      $('#management-fields').append(select('management-mode','Chế độ',[['AUTO_OFF','Tự động tắt'],['AUTO_RENEW','Tự động gia hạn']],p.mode),field('management-period','Chu kỳ tiếp theo (phút)','number',p.period_minutes),
        select('management-ending','Chính sách kết thúc',canPause(env)?[['BACKUP_RETIRE','Sao lưu rồi thu hồi'],['PAUSE','Tạm dừng, giữ dữ liệu']]:[['BACKUP_RETIRE','Sao lưu rồi thu hồi']],p.ending_policy),
        field('management-budget','Giới hạn dự toán cộng dồn của môi trường (USD)','number',p.budget_limit_milliusd==null?Math.max(10,Number(managed.estimated_committed_milliusd||managed.estimate?.total_milliusd||0)/1000):Number(p.budget_limit_milliusd)/1000));
      $('#management-period').min=5;$('#management-period').max=480;$('#management-period').step=1;$('#management-budget').min='.001';$('#management-budget').step='.001';
    }
    for(const control of $('#management-fields').querySelectorAll('input,select'))control.addEventListener('change',updateManagementQuote);
    $('#management-dialog').showModal();await updateManagementQuote();
  }
  $('#management-form').onsubmit=async event=>{
    event.preventDefault();if(!currentQuote?.approved||!$('#management-confirmed').checked)return;$('#management-submit').disabled=true;
    const chosen=request();const body=action==='change_policy'?{operating_policy:chosen.operating_policy,expected_revision:Number(managed.lifecycle_revision||0)}:
      ['backup','destroy'].includes(action)?{}:{confirmed:true,expected_revision:Number(managed.lifecycle_revision||0),...(action==='renew'?{minutes:chosen.minutes}:{})};
    try{const op=await mutate('/environments/'+managed.environment_id+'/'+action,body);track(op);$('#management-dialog').close();await refresh();}
    catch(error){$('#management-notice').textContent=error.message;}finally{$('#management-submit').disabled=false;}
  };
  $('#management-close').onclick=()=>{$('#management-dialog').close();serial++;currentQuote=null;};
  async function detail(env){
    const [fresh,history]=await Promise.all([api('/environments/'+env.environment_id),api('/environments/'+env.environment_id+'/renewals')]);const p=operatingPolicy(fresh);const host=$('#environment-detail');host.replaceChildren();
    for(const [label,value] of [['Môi trường',fresh.environment_id],['Chế độ',modeName(p.mode)],['Chu kỳ',p.period_minutes+' phút'],['Chính sách kết thúc',endingName(p.ending_policy)],
      ['Mốc xử lý tiếp theo',fresh.status==='PAUSED'?'Đã tạm dừng; chưa có lịch vận hành':timestamp(fresh.deadline)],['Kỳ dự toán đang xem',(fresh.estimate_hours||fresh.duration_hours)+' giờ, độc lập deadline'],
      ['Dự toán kỳ đầu',usd(fresh.first_period_estimate?.total_milliusd??fresh.estimate?.total_milliusd)],['Dự toán đã cam kết cộng dồn',usd(fresh.estimated_committed_milliusd??fresh.estimate?.total_milliusd)],
      ['Giới hạn gia hạn cộng dồn',p.budget_limit_milliusd==null?'Không bật tự động gia hạn':usd(p.budget_limit_milliusd)],['Đồng bộ lịch',fresh.schedule_pending?'Đang cần đồng bộ; có thể thử lại tác vụ':'Đã xử lý lịch hiện tại']])host.append(element('p',label+': '+value));
    host.append(element('p','Giới hạn dự toán không bảo đảm hóa đơn thực tế. Môi trường tạm dừng còn phí EBS; bản sao và log sau thu hồi cũng có thể có phí.'));
    if(fresh.renewal_blocked_reason)host.append(element('p','Không gia hạn: '+fresh.renewal_blocked_reason.message,'warning'));
    if(fresh.cost_plan?.after_shutdown)host.append(element('p','Giữ dữ liệu sau kết thúc: '+usd(fresh.cost_plan.after_shutdown.total_milliusd)+' / '+fresh.cost_plan.after_shutdown.period_hours+' giờ (dự toán).'));
    host.append(element('h3','Lịch sử gia hạn'));for(const op of history.items)host.append(element('p',timestamp(op.created_at)+' · '+(op.automatic?'Tự động':'Thủ công')+' · '+op.status+' · '+usd(op.period_estimate?.total_milliusd)+' · Mốc mới '+timestamp(op.deadline)));
    if(!history.items.length)host.append(element('p','Chưa có lần gia hạn.'));if(history.truncated)host.append(element('p','Hiển thị 100 lần gần nhất trong '+history.total_count+' tác vụ gia hạn đã lưu.'));
    const report=fresh.status==='PAUSED'?fresh.pause_report:fresh.cleanup_report;if(report){
      for(const [key,label] of [['deleted','Tài nguyên đã xóa'],['retained','Giữ theo chính sách'],['residual','Tài nguyên còn sót'],['unknown','Chưa xác minh']])if(Array.isArray(report[key]))host.append(element('p',label+': '+report[key].length));
      if(report.cost_note)host.append(element('p',report.cost_note));
      if(report.billable?.length)host.append(element('p','Phần có thể tiếp tục phát sinh phí: '+report.billable.map(row=>row.type==='AWS::EC2::Volume'?'Ổ đĩa EBS '+(row.logical_id||row.id):row.type||String(row)).join(', ')));
      const d=element('details');d.append(element('summary','Tài nguyên đã xóa, giữ, còn sót và phần có phí'),element('pre',JSON.stringify(report,null,2)));host.append(d);
    }
    $('#environment-detail-dialog').showModal();
  }
  $('#environment-detail-close').onclick=()=>$('#environment-detail-dialog').close();
  function controls(card,env){
    const add=(label,fn,disabled=false)=>{const b=element('button',label);b.disabled=disabled;b.onclick=()=>Promise.resolve(fn()).catch(error=>notice(error.message));card.append(b);};
    add('Chi tiết và lịch sử',()=>detail(env));
    if(['READY','PAUSED'].includes(env.status))add('Đổi chế độ',()=>openManagement(env,'change_policy'),!!env.lock||!!env.schedule_pending);
    if(env.status==='READY'){add('Gia hạn thủ công',()=>openManagement(env,'renew'),!!env.lock||!!env.schedule_pending);add('Kết thúc theo chính sách',()=>openManagement(env,'end'),!!env.lock);add('Tạo bản sao',()=>openManagement(env,'backup'),!!env.lock);}
    if(env.status==='PAUSED')add('Mở lại môi trường',()=>openManagement(env,'resume_paused'),!!env.lock);
    if(['READY','PAUSED','ERROR_RETAINED','CREATE_FAILED'].includes(env.status))add('Sao lưu rồi thu hồi',()=>openManagement(env,'destroy'),!!env.lock);
  }
  return {configureCreate,toggleCreate,createRequest,renderQuote,controls,detail,openManagement};
}
