/**
 * Finn Voice Relay Server for Grok xAI Realtime API
 *
 * WebSocket relay between browser/Swift and wss://api.x.ai/v1/realtime
 * Browsers cannot send Authorization headers in WebSocket connections.
 * This relay handles auth server-side and pipes audio bidirectionally.
 *
 * Architecture:
 *   Client <--WebSocket--> This Relay <--WebSocket--> wss://api.x.ai/v1/realtime
 *
 * Tool Batching:
 *   - Collect all tool calls from a response batch
 *   - Execute all tools (Promise.all for MCP tools)
 *   - Send all results to xAI
 *   - Send ONE response.create to continue
 *
 * Config: D:/finn/finn-voice/config/finn-voice-config.json
 * Logs:   D:/finn/finn-voice/logs/grok-relay.log
 */

const WebSocket = require('ws');
const http = require('http');
const url = require('url');
const express = require('express');
const fs = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// CONFIG
// ---------------------------------------------------------------------------
const CONFIG_PATH = path.join(__dirname, 'config', 'finn-voice-config.json');
const LOG_PATH = path.join(__dirname, 'logs', 'grok-relay.log');

let config;
try {
  config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
} catch (err) {
  console.error(`FATAL: Cannot read config at ${CONFIG_PATH}: ${err.message}`);
  process.exit(1);
}

const XAI_API_KEY = config.xai_api_key;
if (!XAI_API_KEY) {
  console.error('FATAL: xai_api_key not set in finn-voice-config.json');
  process.exit(1);
}

const PORT = config.grok_relay_port || 8081;
const VOICE = config.tts_voice || 'tbd';
const XAI_API_URL = 'wss://api.x.ai/v1/realtime';

const FINN_ROUTER_PORT = config.finn_router_port || 9999;
const MCP_EXECUTOR_URL = config.mcp_executor_url || `http://localhost:${FINN_ROUTER_PORT}/api/mcp/execute`;
const CHAT_HISTORY_URL = config.chat_history_url || `http://localhost:${FINN_ROUTER_PORT}/api/chat/history`;

// ---------------------------------------------------------------------------
// LOGGER - writes to console AND log file with ISO timestamps
// ---------------------------------------------------------------------------
function log(level, sessionId, msg) {
  const ts = new Date().toISOString();
  const prefix = sessionId ? `[${ts}] [${level}] [session:${sessionId}]` : `[${ts}] [${level}]`;
  const line = `${prefix} ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(LOG_PATH, line + '\n');
  } catch (_) {
    // If log write fails, console output is still there
  }
}

function logInfo(sessionId, msg) { log('INFO', sessionId, msg); }
function logError(sessionId, msg) { log('ERROR', sessionId, msg); }
function logWarn(sessionId, msg) { log('WARN', sessionId, msg); }
function logDebug(sessionId, msg) { log('DEBUG', sessionId, msg); }

// ---------------------------------------------------------------------------
// EXPRESS APP
// ---------------------------------------------------------------------------
const app = express();
app.use(express.json());

// Track active voice session (assumes one session at a time)
let activeVoiceSession = null;

// Global pending tool calls - cleared on disconnect
let globalPendingToolCalls = new Map();

// Fire-and-forget tools - execute but don't wait for result, don't send to xAI
const FIRE_AND_FORGET_TOOLS = ['chat_structured_message', 'whatsapp_send_message', 'gmail_send_email'];

const server = http.createServer(app);

// ---------------------------------------------------------------------------
// ENDPOINTS
// ---------------------------------------------------------------------------

// Health endpoint
app.get('/health', (req, res) => {
  logInfo(null, 'Health check requested');
  res.json({ status: 'ok', provider: 'XAI Grok Voice', port: PORT });
});

// Inject text into active voice session
app.post('/inject-text', (req, res) => {
  const { text } = req.body;

  if (!activeVoiceSession || !activeVoiceSession.sessionReady) {
    logWarn(null, 'Inject-text requested but no active voice session');
    return res.status(404).json({ error: 'No active voice session' });
  }

  const { xaiWs, sessionId } = activeVoiceSession;

  logInfo(sessionId, `Injecting text into active session: "${(text || '').substring(0, 80)}..."`);

  // Inject text using conversation.item.create
  xaiWs.send(JSON.stringify({
    type: 'conversation.item.create',
    item: {
      type: 'message',
      role: 'user',
      content: [{ type: 'input_text', text }]
    }
  }));

  // Trigger response
  xaiWs.send(JSON.stringify({ type: 'response.create' }));

  logInfo(sessionId, 'Text injected and response.create sent');
  res.json({ success: true });
});

// GLOBAL EMERGENCY HALT - Disconnect ALL voice sessions
app.post('/disconnect-all', (req, res) => {
  logWarn(null, 'Emergency disconnect requested - closing ALL voice sessions');

  // Clear all pending tool calls
  const pendingToolsCleared = globalPendingToolCalls.size;
  globalPendingToolCalls.clear();
  logInfo(null, `Cleared ${pendingToolsCleared} pending tool calls`);

  let sessionsDisconnected = 0;
  const sessions = [];

  // Close active voice session if exists
  if (activeVoiceSession) {
    try {
      activeVoiceSession.clientWs.close(1000, 'Emergency disconnect');
      if (activeVoiceSession.xaiWs) {
        activeVoiceSession.xaiWs.close();
      }
      sessionsDisconnected++;
      sessions.push(`session_${activeVoiceSession.sessionId}`);
      logInfo(activeVoiceSession.sessionId, 'Active session closed via emergency halt');
      activeVoiceSession = null;
    } catch (e) {
      logError(null, `Error closing active session: ${e.message}`);
    }
  }

  // Close all other WebSocket connections
  wss.clients.forEach((client) => {
    try {
      if (client.readyState === WebSocket.OPEN) {
        client.close(1000, 'Emergency disconnect');
        sessionsDisconnected++;
        sessions.push('unknown_session');
      }
    } catch (e) {
      // Ignore close errors
    }
  });

  logInfo(null, `Disconnected ${sessionsDisconnected} voice sessions total`);
  res.json({
    success: true,
    sessions_disconnected: sessionsDisconnected,
    sessions: sessions,
    message: `Disconnected ${sessionsDisconnected} voice sessions`
  });
});

// Fallback for non-API requests
app.use((req, res) => {
  res.status(404).send('Not Found');
});

// ---------------------------------------------------------------------------
// WEBSOCKET SERVER
// ---------------------------------------------------------------------------
const wss = new WebSocket.Server({ server });
let sessionCount = 0;

wss.on('connection', async (clientWs, req) => {
  const sessionId = ++sessionCount;
  const query = url.parse(req.url, true).query;

  // Voice: config > query param > default
  const queryVoice = query.voice;
  const voice = VOICE || queryVoice || 'af_bella';
  let instructions = null;
  const sampleRate = parseInt(query.sampleRate) || 24000;

  logInfo(sessionId, `New client connected - Voice: ${voice}, SampleRate: ${sampleRate}Hz, RemoteAddr: ${req.socket.remoteAddress}`);

  // Track this as the active voice session
  activeVoiceSession = { sessionId, clientWs, xaiWs: null, sessionReady: false };

  let xaiWs = null;
  let isConfigured = false;
  let tools = [];
  let chatHistory = [];
  let initialGreetingInstructions = '';
  let sessionReady = false;
  let botName = 'core_bot';

  // Clear pending tool calls at session start
  globalPendingToolCalls.clear();
  logDebug(sessionId, 'Cleared global pending tool calls for new session');

  // ---------------------------------------------------------------------------
  // Connect to xAI - called AFTER instructions received
  // ---------------------------------------------------------------------------
  function connectToXAI() {
    logInfo(sessionId, `Connecting to xAI Realtime API with instructions (${instructions.length} chars)...`);

    xaiWs = new WebSocket(XAI_API_URL, {
      headers: {
        'Authorization': `Bearer ${XAI_API_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    xaiWs.on('open', () => {
      logInfo(sessionId, 'Connected to xAI Realtime API');
      clientWs.send(JSON.stringify({ type: 'relay.connected' }));
    });

    xaiWs.on('message', async (data) => {
      try {
        const message = JSON.parse(data.toString());
        const eventType = message.type;

        // Log all non-audio events
        if (eventType !== 'response.output_audio.delta' && eventType !== 'input_audio_buffer.append') {
          logDebug(sessionId, `xAI -> Client: ${eventType}`);
        }

        // Configure session on conversation.created
        if (eventType === 'conversation.created' && !isConfigured) {
          logInfo(sessionId, `Configuring session with context and ${tools.length} tools...`);

          const sessionConfig = {
            type: 'session.update',
            session: {
              instructions,
              voice,
              audio: {
                input: { format: { type: 'audio/pcm', rate: sampleRate } },
                output: { format: { type: 'audio/pcm', rate: sampleRate } }
              },
              turn_detection: {
                type: 'server_vad',
                silence_duration_ms: 1500,
                threshold: 0.5
              }
            }
          };

          // Add tools if any are enabled
          if (tools.length > 0) {
            sessionConfig.session.tools = tools;
          }

          logInfo(sessionId, `Tools in session: ${tools.length > 0 ? tools.map(t => t.name).join(', ') : 'none'}`);
          logDebug(sessionId, `Session config sent: instructions=${instructions.length} chars, voice=${voice}, sampleRate=${sampleRate}`);
          xaiWs.send(JSON.stringify(sessionConfig));
          isConfigured = true;
        }

        if (eventType === 'session.updated') {
          logInfo(sessionId, 'Session updated by xAI');

          // STEP 1: Inject chat history
          if (chatHistory.length > 0) {
            logInfo(sessionId, `Injecting ${chatHistory.length} history items...`);
            for (const msg of chatHistory) {
              xaiWs.send(JSON.stringify({
                type: 'conversation.item.create',
                item: {
                  type: 'message',
                  role: msg.role,
                  content: [{ type: 'input_text', text: msg.text }]
                }
              }));
            }
            logDebug(sessionId, `All ${chatHistory.length} history items injected`);
          }

          // STEP 2: Inject initial greeting instructions if set
          if (initialGreetingInstructions) {
            logInfo(sessionId, `Injecting initial greeting instructions (${initialGreetingInstructions.length} chars)...`);
            xaiWs.send(JSON.stringify({
              type: 'conversation.item.create',
              item: {
                type: 'message',
                role: 'system',
                content: [{ type: 'input_text', text: `[Session Start Instructions]: ${initialGreetingInstructions}` }]
              }
            }));
          }

          // STEP 3: Trigger AI greeting (speaks first)
          logInfo(sessionId, 'Triggering AI greeting via response.create...');
          xaiWs.send(JSON.stringify({ type: 'response.create' }));

          // STEP 4: Signal ready and enable audio gate
          sessionReady = true;
          logInfo(sessionId, 'Audio gate OPEN - session ready for voice');
          clientWs.send(JSON.stringify({ type: 'relay.ready' }));

          // Update active session tracking
          if (activeVoiceSession && activeVoiceSession.sessionId === sessionId) {
            activeVoiceSession.xaiWs = xaiWs;
            activeVoiceSession.sessionReady = true;
          }
        }

        // Forward message to client
        if (clientWs.readyState === WebSocket.OPEN) {
          clientWs.send(data.toString());
        }

        if (eventType === 'error') {
          logError(sessionId, `xAI Error: ${JSON.stringify(message.error || message)}`);
        }

        // TOOL BATCHING: On response.output_item.done with function_call - STORE, don't execute yet
        if (eventType === 'response.output_item.done' && message.item?.type === 'function_call') {
          const callId = message.item.call_id;
          const fnName = message.item.name;
          const fnArgs = message.item.arguments;

          logInfo(sessionId, `Tool call QUEUED: ${fnName} | call_id=${callId} | args=${(fnArgs || '').substring(0, 100)}`);

          globalPendingToolCalls.set(callId, { name: fnName, args: fnArgs });
          logDebug(sessionId, `Pending tool calls in queue: ${globalPendingToolCalls.size}`);
        }

        // TOOL BATCHING: On response.done - execute ALL pending tools, send results, ONE response.create
        if (eventType === 'response.done' && globalPendingToolCalls.size > 0) {
          const batchTimestamp = new Date().toISOString();
          logInfo(sessionId, `--- TOOL BATCH START --- Executing ${globalPendingToolCalls.size} queued tools at ${batchTimestamp}`);

          const resultsToSend = [];

          for (const [callId, { name: fnName, args: fnArgs }] of globalPendingToolCalls) {
            try {
              const parsedArgs = JSON.parse(fnArgs);

              // FIRE AND FORGET
              if (FIRE_AND_FORGET_TOOLS.includes(fnName)) {
                logInfo(sessionId, `Fire-and-forget: ${fnName} | call_id=${callId}`);

                // chat_structured_message: Save CONTENT to chat history
                if (fnName === 'chat_structured_message' && parsedArgs.content) {
                  logDebug(sessionId, `Saving structured message to chat history: ${(parsedArgs.content || '').substring(0, 80)}...`);
                  fetch(CHAT_HISTORY_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      botName,
                      role: 'assistant',
                      content: parsedArgs.content,
                      source: 'voice'
                    })
                  }).catch(err => logError(sessionId, `Failed to save structured message: ${err.message}`));
                }
                // send_message, send_email: Execute via MCP AND save to chat history
                else if (fnName === 'whatsapp_send_message' || fnName === 'gmail_send_email') {
                  logDebug(sessionId, `Executing fire-and-forget MCP tool: ${fnName}`);
                  fetch(MCP_EXECUTOR_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tool: fnName, arguments: parsedArgs })
                  }).catch(err => logError(sessionId, `Fire-and-forget tool error (${fnName}): ${err.message}`));

                  // Save tool args to chat history
                  const toolDisplay = `Executed: ${fnName}\nArgs: ${JSON.stringify(parsedArgs, null, 2)}`;
                  fetch(CHAT_HISTORY_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      botName,
                      role: 'tool',
                      content: toolDisplay,
                      source: 'voice'
                    })
                  }).catch(err => logError(sessionId, `Failed to save tool execution to history: ${err.message}`));
                }
                // Other fire-and-forget: just execute
                else {
                  logDebug(sessionId, `Executing generic fire-and-forget: ${fnName}`);
                  fetch(MCP_EXECUTOR_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tool: fnName, arguments: parsedArgs })
                  }).catch(err => logError(sessionId, `Fire-and-forget tool error (${fnName}): ${err.message}`));
                }

                logInfo(sessionId, `Fire-and-forget complete: ${fnName} | call_id=${callId}`);
                // DON'T add to resultsToSend
                continue;
              }

              // PROMISE WAIT: Execute via MCP executor, wait for result, send to xAI
              logInfo(sessionId, `Executing MCP tool (promise-wait): ${fnName} | call_id=${callId}`);
              const mcpRes = await fetch(MCP_EXECUTOR_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tool: fnName, arguments: parsedArgs })
              });

              const mcpData = await mcpRes.json();
              const output = mcpData.success
                ? JSON.stringify(mcpData.result || { success: true })
                : JSON.stringify({ error: mcpData.error || 'Tool execution failed' });

              logInfo(sessionId, `Tool result for ${fnName}: ${output.substring(0, 150)}...`);

              resultsToSend.push({ callId, output });

            } catch (err) {
              logError(sessionId, `Tool execution error for call_id=${callId}: ${err.message}`);
              resultsToSend.push({ callId, output: JSON.stringify({ error: err.message }) });
            }
          }

          // Send ALL function_call_output to xAI (for promise_wait tools only)
          for (const { callId, output } of resultsToSend) {
            logDebug(sessionId, `Sending function_call_output to xAI: call_id=${callId}`);
            xaiWs.send(JSON.stringify({
              type: 'conversation.item.create',
              item: {
                type: 'function_call_output',
                call_id: callId,
                output: output
              }
            }));
          }

          // Send ONE response.create to continue (only if we have results to send)
          if (resultsToSend.length > 0) {
            logInfo(sessionId, `All ${resultsToSend.length} tool results sent to xAI - triggering ONE response.create`);
            xaiWs.send(JSON.stringify({ type: 'response.create' }));
          } else {
            logInfo(sessionId, 'All tools were fire-and-forget - no response.create needed');
          }

          // Clear pending tools for next batch
          globalPendingToolCalls.clear();
          logInfo(sessionId, '--- TOOL BATCH END ---');
        }
      } catch (err) {
        logError(sessionId, `Error processing xAI message: ${err.message}\n${err.stack}`);
      }
    });

    xaiWs.on('error', (err) => {
      logError(sessionId, `xAI WebSocket error: ${err.message}`);
      clientWs.send(JSON.stringify({ type: 'relay.error', error: err.message }));
    });

    xaiWs.on('close', (code, reason) => {
      logInfo(sessionId, `xAI connection closed - Code: ${code}, Reason: ${reason || 'No reason'}`);
      if (clientWs.readyState === WebSocket.OPEN) {
        clientWs.close(code, reason);
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Handle messages from client
  // ---------------------------------------------------------------------------
  clientWs.on('message', (data) => {
    try {
      const message = JSON.parse(data.toString());

      // WAIT for instructions before connecting to xAI
      if (message.type === 'voice.set_instructions') {
        instructions = message.instructions;
        tools = message.tools || [];
        chatHistory = message.chatHistory || [];
        initialGreetingInstructions = message.initialGreetingInstructions || '';
        botName = message.botName || 'core_bot';
        logInfo(sessionId, `Received voice.set_instructions: ${instructions.length} chars, ${tools.length} tools, ${chatHistory.length} history items, botName=${botName}`);

        // NOW connect to xAI with instructions ready
        if (!xaiWs) {
          connectToXAI();
        }
        return;
      }

      if (message.type !== 'input_audio_buffer.append') {
        logDebug(sessionId, `Client -> xAI: ${message.type}`);
      }

      // Gate audio until session is fully configured
      if (message.type === 'input_audio_buffer.append' && !sessionReady) {
        logDebug(sessionId, 'Audio gate CLOSED - dropping audio frame');
        return;
      }

      if (xaiWs && xaiWs.readyState === WebSocket.OPEN) {
        xaiWs.send(data.toString());
      }
    } catch (err) {
      // Binary data (raw audio) - forward as-is
      if (xaiWs && xaiWs.readyState === WebSocket.OPEN) {
        xaiWs.send(data);
      }
    }
  });

  clientWs.on('close', (code, reason) => {
    logInfo(sessionId, `Client disconnected - Code: ${code}, Reason: ${reason || 'No reason'}`);
    // Clear pending tool calls on disconnect
    const pendingCleared = globalPendingToolCalls.size;
    globalPendingToolCalls.clear();
    if (pendingCleared > 0) {
      logInfo(sessionId, `Cleared ${pendingCleared} pending tool calls on disconnect`);
    }
    // Clear active session if this was it
    if (activeVoiceSession && activeVoiceSession.sessionId === sessionId) {
      activeVoiceSession = null;
      logInfo(sessionId, 'Active voice session cleared');
    }
    if (xaiWs && xaiWs.readyState === WebSocket.OPEN) {
      xaiWs.close();
      logInfo(sessionId, 'Closed xAI WebSocket due to client disconnect');
    }
  });

  clientWs.on('error', (err) => {
    logError(sessionId, `Client WebSocket error: ${err.message}`);
    if (xaiWs && xaiWs.readyState === WebSocket.OPEN) {
      xaiWs.close();
    }
  });
});

// ---------------------------------------------------------------------------
// START SERVER
// ---------------------------------------------------------------------------
server.listen(PORT, () => {
  logInfo(null, '========================================');
  logInfo(null, ' Finn Voice Relay Server (Grok xAI)');
  logInfo(null, ` Port: ${PORT}`);
  logInfo(null, ` xAI API: ${XAI_API_URL}`);
  logInfo(null, ` MCP Executor: ${MCP_EXECUTOR_URL}`);
  logInfo(null, ` Chat History: ${CHAT_HISTORY_URL}`);
  logInfo(null, ` Voice: ${VOICE}`);
  logInfo(null, ` Log file: ${LOG_PATH}`);
  logInfo(null, '========================================');
  logInfo(null, 'Waiting for connections...');
});

process.on('SIGINT', () => {
  logInfo(null, 'SIGINT received - shutting down...');
  wss.clients.forEach((client) => client.close());
  server.close(() => {
    logInfo(null, 'Server closed. Goodbye.');
    process.exit(0);
  });
});

process.on('uncaughtException', (err) => {
  logError(null, `Uncaught exception: ${err.message}\n${err.stack}`);
});

process.on('unhandledRejection', (reason) => {
  logError(null, `Unhandled rejection: ${reason}`);
});
