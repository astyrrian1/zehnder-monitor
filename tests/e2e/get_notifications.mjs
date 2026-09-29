// Read-only HA WebSocket query for the isolated test instance.
const base = process.env.ZMON_TEST_HA_URL;
if (!base?.endsWith(':18123')) throw new Error('Refusing non-isolated HA URL');
const token = process.env.ZMON_TEST_HA_TOKEN;
const ws = new WebSocket(base.replace(/^http/, 'ws') + '/api/websocket');
const notifications = await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error('HA WebSocket timeout')), 10000);
  ws.onerror = reject;
  ws.onmessage = ({data}) => {
    const message = JSON.parse(data);
    if (message.type === 'auth_required') ws.send(JSON.stringify({type:'auth', access_token:token}));
    else if (message.type === 'auth_ok') ws.send(JSON.stringify({id:1, type:'persistent_notification/get'}));
    else if (message.type === 'auth_invalid') reject(new Error('HA WebSocket authentication rejected'));
    else if (message.id === 1) {
      clearTimeout(timeout);
      if (!message.success) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
      ws.close();
    }
  };
});
console.log(JSON.stringify(notifications));
