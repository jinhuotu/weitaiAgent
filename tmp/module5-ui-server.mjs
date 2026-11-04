import fs from 'node:fs';
import {createServer} from 'file:///E:/code/python/weitaiAgentui/node_modules/vite/dist/node/index.js';
const api=JSON.parse(fs.readFileSync('tmp/module5-smoke-ready.json')).url;
const server=await createServer({root:'E:/code/python/weitaiAgentui',configFile:'E:/code/python/weitaiAgentui/vite.config.ts',cacheDir:'E:/code/python/weitaiAgent/tmp/module5-vite-cache',server:{host:'127.0.0.1',port:0,open:false,proxy:{'/api':{target:api,changeOrigin:true}}},clearScreen:false});
await server.listen();const port=server.httpServer.address().port;fs.writeFileSync('tmp/module5-ui-ready.json',JSON.stringify({url:`http://127.0.0.1:${port}`}));console.log('Isolated frontend verification server',port);
