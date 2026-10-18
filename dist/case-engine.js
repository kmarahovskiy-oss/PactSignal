/* Synthetic event reducer. Explicit structured events; no free-text understanding claimed. */
(function(root){
const secondDefaults={quote:80,cap:50,extras:'conditional',deadline:'requested',owner:'Alex'};
const defaults={quote:180,cap:150,extras:'conditional',deadline:'requested',claimRef:'unknown',receipt:100,owner:'Nina'};
function evaluate(input={}){
 const x={...defaults,...input};const z={...secondDefaults,...input.second};
 for(const k of ['quote','cap'])if(!Number.isFinite(z[k])||z[k]<0||z[k]>10000||Math.abs(z[k]*100-Math.round(z[k]*100))>1e-7)throw Error('Second-order amount must be between 0 and 10,000 with at most two decimal places.');
 if(!['conditional','approved','withdrawn'].includes(z.extras)||!['requested','accepted'].includes(z.deadline)||!['Alex','Nina'].includes(z.owner))throw Error('Unknown second-order state');
 for(const key of ['quote','cap','receipt'])if(!Number.isFinite(x[key])||x[key]<0||x[key]>10000)throw Error('Amount must be between 0 and 10,000');
 if(!['conditional','approved','withdrawn'].includes(x.extras)||!['requested','accepted'].includes(x.deadline)||!['unknown','ST-24','ST-25'].includes(x.claimRef)||!['Alex','Nina'].includes(x.owner))throw Error('Unknown scenario state');
 const included=x.extras==='approved'||x.quote<=x.cap;
 const extraState=x.extras==='withdrawn'?(included?'cancellation_pending':'withdrawn'):included?'approved':'needs_approval';
 const money=n=>Math.round((n+Number.EPSILON)*100)/100;
 for(const k of ['quote','cap','receipt'])if(Math.abs(x[k]*100-Math.round(x[k]*100))>1e-7)throw Error('Use at most two decimal places.');
 const total=money(300+(included?x.quote:0)),date=x.deadline==='accepted'?'Thursday':'Friday';
 const events=[{id:'e1',time:'09:00',channel:'Email',who:'Rowan',text:'ST-24: I approve 10 catalogue images for EUR 300, delivery Friday.'},{id:'e2',time:'09:05',channel:'Email',who:'Rowan',text:'ST-25: I approve 6 portrait edits for EUR 240, delivery Monday. This is a separate order.'},{id:'e3',time:'10:00',channel:'Telegram',who:'Rowan',text:`ST-24: I authorize 20 extra images only if the additional fee is no more than EUR ${x.cap}. Can we move delivery to Thursday?`},{id:'e4',time:'10:10',channel:'Email',who:'Studio',text:`ST-24: The fee for 20 additional images is EUR ${x.quote}. Thursday is not confirmed yet.`}];
 if(x.extras==='approved')events.push({id:'e5',time:'10:20',channel:'Telegram',who:'Rowan',text:`ST-24: I explicitly approve the quoted EUR ${x.quote} for 20 additional images; this replaces my earlier fee limit.`});
 if(x.extras==='withdrawn')events.push({id:'e5',time:'10:20',channel:'Telegram',who:'Rowan',text:'ST-24: Withdraw my extra-image request. Keep only the original 10 images. I still request Thursday delivery.'});
 if(x.deadline==='accepted')events.push({id:'e6',time:'10:30',channel:'Email',who:'Studio',text:'ST-24: We confirm the requested Thursday delivery date for the current scope.'});
 events.push({id:'e7',time:'11:00',channel:'Internal',who:'Team lead',text:x.owner==='Nina'?'Alex is unavailable. Nina now owns ST-24. ST-25 stays with Alex.':'Alex continues to own both orders.'},{id:'e8',time:'11:20',channel:'WhatsApp',who:'Rowan',text:`${x.claimRef==='unknown'?'For my order':x.claimRef}: I have sent EUR 300 in total so far. Please check.`},{id:'e9',time:'11:25',channel:'Receipt fixture',who:'Synthetic ledger',text:`Fictional cumulative receipt for ST-24: EUR ${x.receipt}. Reference explicitly identifies ST-24. This is not a real bank record.`});
 events.splice(2,0,{id:'e2a',time:'09:10',channel:'Telegram',who:'Rowan',text:`ST-25: I authorize 4 extra portrait edits only if the fee is at most EUR ${z.cap}. Please move delivery from Monday to Thursday.`},{id:'e2b',time:'09:15',channel:'Email',who:'Studio',text:`ST-25: Four extra edits cost EUR ${z.quote}. Thursday is not confirmed yet.`});
 const secondIncluded=z.extras==='approved'||z.quote<=z.cap,secondTotal=money(240+(secondIncluded?z.quote:0));
 const secondStatus=z.extras==='withdrawn'?(secondIncluded?'cancellation_pending':'withdrawn'):secondIncluded?'approved':'needs_approval';
 const claims=x.claimRef==='unknown'?null:300;
 const cases=[{id:'ST-24',total,images:10+(included?20:0),owner:x.owner,date,receipt:x.receipt,claim:x.claimRef==='ST-24'?claims:null,outstanding:money(Math.max(0,total-x.receipt)),overpayment:money(Math.max(0,x.receipt-total)),unverifiedClaim:x.claimRef==='ST-24'?money(Math.max(0,300-x.receipt)):null,extraState,datePending:x.deadline!=='accepted'}, {id:'ST-25',total:secondTotal,images:6+(secondIncluded?4:0),owner:z.owner,date:z.deadline==='accepted'?'Thursday':'Monday',receipt:0,extraState:secondStatus,datePending:z.deadline!=='accepted',claim:x.claimRef==='ST-25'?claims:null,outstanding:secondTotal,overpayment:0,unverifiedClaim:x.claimRef==='ST-25'?300:null}];
 const alerts=[];if(x.claimRef==='unknown')alerts.push({code:'reference',source:'e8'});if(extraState==='cancellation_pending')alerts.push({code:'cancel',source:'e5'});if(extraState==='needs_approval')alerts.push({code:'scope',source:'e4'});if(x.deadline==='requested')alerts.push({code:'deadline',source:'e3'});if(x.claimRef!=='unknown'&&cases.find(c=>c.id===x.claimRef).unverifiedClaim>0)alerts.push({code:'receipt',source:'e8',caseId:x.claimRef});if(cases[0].overpayment)alerts.push({code:'overpayment',source:'e9'});
 if(secondStatus==='needs_approval')alerts.push({code:'scope',source:'e2b',caseId:'ST-25'});if(secondStatus==='cancellation_pending')alerts.push({code:'cancel',source:'e2a',caseId:'ST-25'});if(z.deadline==='requested')alerts.push({code:'deadline',source:'e2a',caseId:'ST-25'});
 return {cases,events,alerts,unallocatedClaim:x.claimRef==='unknown'?300:0,extraBasis:x.extras==='conditional'?'e3': 'e5',dateBasis:x.deadline==='accepted'?'e6':'e1'};
}
const api={defaults,secondDefaults,evaluate};root.PactCase=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
