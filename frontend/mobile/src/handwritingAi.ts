import { invoke, isTauri } from '@tauri-apps/api/core'
import type { HandwritingAnalysis } from '../../src/features/editor/HandwritingPanel.vue'
export interface AiProfile {endpoint:string;model:string;protocol:'openai_chat'|'openai_responses'|'anthropic_messages'|'ollama';vision:boolean}
export interface AiState {profile:AiProfile|null;credential_configured:boolean;native:boolean;tasks:string[]}
export function aiError(cause:unknown,language='zh-CN'){
  const code=cause instanceof Error?cause.message:String(cause)
  const messages:Record<string,[string,string]>={
    AI_PROFILE_REQUIRED:['请先配置本设备的模型连接。','Configure a model connection on this device first.'],
    AI_CREDENTIAL_REQUIRED:['请在本设备填写此地址的 API Key。','Enter the API key for this endpoint on this device.'],
    AI_AUTH_FAILED:['模型鉴权失败，请检查本设备保存的 API Key。','Model authentication failed. Check the saved API key.'],
    HANDWRITING_VISION_REQUIRED:['笔迹识别需要支持视觉输入的模型。','Ink recognition requires a vision model.'],
    HANDWRITING_EMPTY:['请先添加笔迹或文字。','Add handwriting or text first.'],
    HANDWRITING_INSTRUCTION_REQUIRED:['请输入希望 Agent 编写的内容。','Describe the note you want the Agent to write.'],
    AI_BUSY:['上一请求尚未结束，请稍后再操作。','The previous request is still ending. Try again shortly.'],
    AI_TIMEOUT:['模型请求超时，原笔迹已保留。','The model request timed out. Original ink is preserved.'],
    AI_NETWORK_ERROR:['无法连接模型服务，原笔迹已保留。','Could not reach the model service. Original ink is preserved.'],
    AI_FOREGROUND_CHANGED:['应用已退到后台，本次处理已停止。','The app left the foreground. This task stopped.'],
    AI_RESPONSE_INCOMPLETE:['模型未返回完整结果，请缩短内容后重新生成。','The model returned an incomplete result. Try a shorter note.'],
    AI_PROFILE_INVALID:['连接配置无效，请检查 HTTPS 地址、协议和模型 ID。','Check the HTTPS URL, protocol and model ID.'],
    AI_PLATFORM_UNAVAILABLE:['当前客户端尚未提供原生 AI 通道。','This client does not provide the native AI command.'],
    AI_RATE_LIMITED:['模型服务暂时限流，请稍后再试。','The model service is rate limited. Try again later.'],
  }
  return (messages[code]??['处理未完成，原笔迹已保留。','The task did not complete. Original ink is preserved.'])[language==='en'?1:0]
}
export async function mobileAi<T>(request:Record<string,unknown>):Promise<T>{
  if(!isTauri())throw Error('AI_PLATFORM_UNAVAILABLE')
  const raw=await invoke<string>('mobile_ai',{request:JSON.stringify(request)})
  return JSON.parse(raw) as T
}
export async function analyzeHandwriting(input:HandwritingAnalysis,signal:AbortSignal):Promise<string>{
  signal.throwIfAborted()
  const operation_id=crypto.randomUUID()
  const cancel=()=>{void mobileAi({action:'cancel',operation_id}).catch(()=>undefined)}
  signal.addEventListener('abort',cancel,{once:true})
  try{
    const result=await mobileAi<{text:string;persisted:false}>({action:'analyze',operation_id,input})
    signal.throwIfAborted()
    if(typeof result.text!=='string'||result.persisted!==false)throw Error('AI_RESPONSE_INVALID')
    return result.text
  }finally{signal.removeEventListener('abort',cancel)}
}
