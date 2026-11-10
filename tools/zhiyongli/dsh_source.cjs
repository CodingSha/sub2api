/** 智用力专用DSH工具：按冻结清单读取和提交，不开放任意路径、Shell或联网。 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
exports.name = 'zhiyongli-source';
exports.inject = ['tools', 'agents'];
exports.apply = async ctx => {
  const { defineTool } = await import(require('node:url').pathToFileURL(path.join(process.env.ZHIYONGLI_DSH_RUNTIME, 'node_modules/@deepseek-ai/dsh-tools/lib/index.js')).href);
  const directory = process.env.ZHIYONGLI_DSH_REQUEST;
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'assets.json'), 'utf8'));
  const journal = path.join(directory, 'reads.ndjson');
  const requests = path.join(directory, 'requests.ndjson');
  function append(file, value) {
    const fd = fs.openSync(file, 'a', 0o600);
    try { fs.writeSync(fd, JSON.stringify(value) + '\n'); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
  }
  ctx.on('agent/request', async (event, next) => {
    const count = fs.existsSync(requests) ? fs.readFileSync(requests, 'utf8').trim().split('\n').filter(Boolean).length : 0;
    const limit = Number(process.env.ZHIYONGLI_DSH_STAGE_LIMIT || manifest.max_requests);
    function deny(code, used, cap) {
      append(path.join(directory, 'denials.ndjson'), {at: Date.now(), code, used, limit: cap, turn: event.turn, step: event.step});
      throw new Error(code);
    }
    if (!Number.isSafeInteger(limit) || limit < 1) throw new Error('ZHIYONGLI_INVALID_BUDGET');
    if (count >= limit) deny('ZHIYONGLI_REQUEST_BUDGET', count, limit);
    const jobJournal = process.env.ZHIYONGLI_DSH_JOB_JOURNAL;
    if (jobJournal) {
      const used = fs.existsSync(jobJournal) ? fs.readFileSync(jobJournal, 'utf8').trim().split('\n').filter(Boolean).length : 0;
      if (used >= Number(process.env.ZHIYONGLI_DSH_JOB_LIMIT)) deny('ZHIYONGLI_JOB_BUDGET', used, Number(process.env.ZHIYONGLI_DSH_JOB_LIMIT));
    }
    const config = await next();
    if (config.provider !== 'zhiyongli' || config.model !== manifest.model) throw new Error('ZHIYONGLI_MODEL_DRIFT');
    // 请求发出前记录；崩溃后的计数不归零，不在插件里重试。
    append(requests, {at: Date.now(), turn: event.turn, step: event.step, model: config.model});
    if (jobJournal) append(jobJournal, {at: Date.now(), directory, turn: event.turn, step: event.step});
    return {...config, maxTokens: 24000};
  });
  ctx.tools.register(defineTool({
    name: 'read_assessment_asset',
    description: '读取本次冻结材料的一个完整分段。只接受清单中的asset_id，无路径参数；分段边界不代表目标边界。',
    parameters: {asset_id: {type: 'string', required: true}},
    output: {
      schema: {type: 'string'},
      render: (_args, value) => [{type: 'text', text: value}],
    },
    isConcurrencySafe: () => false,
    async execute(args) {
      const asset = manifest.assets.find(a => a.id === args.asset_id);
      if (!asset || !/^[a-z0-9-]+\.txt$/.test(asset.file)) throw new Error('未知材料');
      const target = path.join(directory, asset.file);
      if (fs.lstatSync(target).isSymbolicLink()) throw new Error('不允许符号链接');
      const data = fs.readFileSync(target);
      if (hash(data) !== asset.sha256) throw new Error('冻结材料签名变化');
      append(journal, {asset_id: asset.id, sha256: asset.sha256, at: Date.now()});
      return data.toString('utf8');
    },
  }));
  ctx.tools.register(defineTool({
    name: 'submit_assessment',
    description: '完整阅读全部材料后，将符合schema的JSON对象通过value提交。每次执行只接受一次；不能通过最终聊天正文交回长JSON。程序随后按原生合同核验，不代表语义已通过。',
    parameters: {value: {type: 'json', required: true}},
    output: {schema: {type: 'string'}, render: (_args, value) => [{type: 'text', text: value}]},
    isConcurrencySafe: () => false,
    async execute(args) {
      const read = new Set(fs.existsSync(journal) ? fs.readFileSync(journal, 'utf8').trim().split('\n').filter(Boolean).map(l => JSON.parse(l).asset_id) : []);
      if (!manifest.assets.every(a => read.has(a.id))) throw new Error('尚未完整读取冻结材料');
      if (!args.value || typeof args.value !== 'object' || Array.isArray(args.value)) throw new Error('结果必须为JSON对象');
      const target = path.join(directory, 'submitted.json');
      if (fs.existsSync(target)) throw new Error('结果已经提交，修订必须新建有理由的执行记录');
      const text = JSON.stringify(args.value);
      const fd = fs.openSync(target, 'wx', 0o600);
      try { fs.writeSync(fd, text); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
      append(path.join(directory, 'submissions.ndjson'), {at: Date.now(), sha256: hash(text)});
      return '已冻结结构化结果；请简短结束，不重复提交，不自行宣布评分通过。';
    },
  }));
  // 工具和请求闸门安装成功后才允许headless启动，导入失败不能退化为无保护调用。
  ctx.provide('zhiyongliSource', {});
};
