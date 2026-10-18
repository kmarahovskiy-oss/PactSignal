// Message-to-event suggestions are deliberately narrow and require explicit confirmation.
(function(root){
function suggest(text,role){
 // Closed, anchored grammar: do not turn arbitrary prose, questions, conditions or other currencies into confirmed events.
 const head=text.trim().match(/^(ST-(?:24|25))\s*:\s*([\s\S]+)$/i);if(!head)return null;
 const body=head[2].trim();let m;
 if(role==='client'){
  if(/^(?:I (?:have )?)?(?:sent|paid) (?:EUR\s*|€\s*)300(?:[.,]00)?(?: in total(?: so far)?)?[.!]?$/i.test(body)||/^(?:Я )?(?:отправил|оплатил) 300(?:[.,]00)? евро(?: всего)?[.!]?$/i.test(body))return {action:'claim',value:head[1].toUpperCase()};
  if((m=body.match(/^(?:I )?approve (?:EUR\s*|€\s*)(\d+(?:[.,]\d{1,2})?)(?: for (?:the )?(?:extra|additional) (?:images|work))?[.!]?$/i))||(m=body.match(/^(?:Я )?согласовываю (\d+(?:[.,]\d{1,2})?) евро(?: за допработы)?[.!]?$/i)))return {action:'approve',value:Number(m[1].replace(',','.'))};
  if(/^(?:I )?withdraw (?:my|the) extra(?:-image| image|-work| work) request[.!]?$/i.test(body)||/^(?:Я )?отзываю запрос на допработы[.!]?$/i.test(body))return {action:'withdraw',value:'withdrawn'};
  if(/^(?:Thursday please|Can we (?:deliver|move delivery to) Thursday\?|Please deliver Thursday|Прошу срок в четверг)[.!]?$/i.test(body))return {action:'request_date',value:'requested'};
 }
 if(role==='manager'){
  if(/^(?:Thursday confirmed|We confirm Thursday delivery|Подтверждаем срок в четверг)[.!]?$/i.test(body))return {action:'confirm_date',value:'accepted'};
  if((m=body.match(/^(Nina|Alex) takes over as owner[.!]?$/i)))return {action:'owner',value:m[1].toLowerCase()==='nina'?'Nina':'Alex'};
 }
 return null;
}
function apply(state,event){const next={...state};if(!['ST-24','ST-25'].includes(event.caseId))throw Error('Specify the order.');if(event.caseId==='ST-25'&&event.action!=='claim'){const local={quote:80,cap:50,extras:'conditional',deadline:'requested',owner:'Alex',...state.second};const changed=apply(local,{...event,caseId:'ST-24'});next.second=changed;return next;}
 if(event.action==='approve'){if(event.role!=='client'||event.value!==state.quote)throw Error('Approval must match the current quote and be from the client.');next.extras='approved'}
 else if(event.action==='withdraw'){if(event.role!=='client')throw Error('Only the client can withdraw this request.');if(state.extras==='approved')throw Error('The additional work is already approved. Agree cancellation separately; the approved amount is preserved.');next.extras='withdrawn'}
 else if(event.action==='claim'){if(event.role!=='client')throw Error('A client claim is required.');if(!['ST-24','ST-25'].includes(event.value)||event.caseId!==event.value)throw Error('Specify the order.');next.claimRef=event.value}
 else if(event.action==='request_date'){if(event.role!=='client')throw Error('Client request required.');if(state.deadline!=='accepted')next.deadline='requested'}
 else if(event.action==='confirm_date'){if(event.role!=='manager')throw Error('Studio confirmation required.');next.deadline='accepted'}
 else if(event.action==='owner'){if(event.role!=='manager'||!['Nina','Alex'].includes(event.value))throw Error('Manager confirmation and known owner required.');next.owner=event.value}
 else throw Error('No supported event was confirmed.');return next;
}
root.PactChat={suggest,apply};if(typeof module!=='undefined')module.exports=root.PactChat;
})(typeof window!=='undefined'?window:globalThis);
