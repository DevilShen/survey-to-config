-- 软件开发全流程决策系统 · 数据结构
-- 设计原则：只存原始事实（人天流水、变更单、工单、问卷答案），
--          所有指标（CPI / EAC / 五项分解 / RICE / 配置）在查询时计算。
--          避免"把结论存成字段"，否则口径一改，历史数据全部作废。

PRAGMA foreign_keys = ON;

/* ---------------- 组织与权限 ---------------- */

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,
  role          TEXT NOT NULL CHECK (role IN ('admin', 'pm', 'finance', 'viewer')),
  created_at    TEXT NOT NULL,
  -- 登录账号：demo 账号用短账号名（demo），种子用户用邮箱前缀
  login_name    TEXT,
  -- PBKDF2-HMAC-SHA256：pbkdf2_sha256$轮数$盐$哈希，绝不存明文
  password_hash TEXT
);
-- 注意：login_name 的唯一索引不写在这里。老库的表已存在（CREATE TABLE IF NOT EXISTS 会跳过），
-- 直接建索引会因缺列报 "no such column"，统一由 db._migrate() 补列后再建。

/* ---------------- 项目 ---------------- */

CREATE TABLE IF NOT EXISTS projects (
  id                      TEXT PRIMARY KEY,
  name                    TEXT NOT NULL,
  client                  TEXT NOT NULL,
  mode                    TEXT NOT NULL,              -- 定制交付 / 标准配置
  status                  TEXT NOT NULL,              -- 进行中 / 预警 / 高危 / 关注 / 结项
  contract_amount         REAL NOT NULL,              -- 合同额（万元）
  budget_days             REAL NOT NULL,              -- BAC：预算人天
  estimate_days           REAL NOT NULL,              -- E：初始估算人天
  baseline_predicted_days REAL NOT NULL,              -- 估算基线表给出的预测值（用于⑤基线偏差）
  progress                REAL NOT NULL,              -- 完工比例 0-1
  detail_level            TEXT NOT NULL DEFAULT 'summary', -- full=已做五项分解 / summary=仅汇总
  data_quality            TEXT NOT NULL DEFAULT 'ok', -- ok / gap（阻塞记录不全，分解可信度低）
  reported_days           REAL NOT NULL DEFAULT 0,    -- 财务/工时系统上报的实际总人天（权威口径）
  start_date              TEXT,
  end_date                TEXT,
  pm_id                   TEXT REFERENCES users(id)
);

-- 人天流水：一切成本指标的唯一事实来源
-- category 语义：
--   base        按初始估算完成的计划内工作量
--   scope       已审批变更带来的净增工作量
--   rework      需求二次开发（同一需求重复交付）
--   efficiency  毛效率损失（含返工），净效率 = efficiency - rework
--   wait        阻塞等待（客户确认、依赖方、环境不可用）
CREATE TABLE IF NOT EXISTS effort_records (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id      TEXT NOT NULL REFERENCES projects(id),
  requirement_id  TEXT,
  category        TEXT NOT NULL CHECK (category IN ('base', 'scope', 'rework', 'efficiency', 'wait')),
  days            REAL NOT NULL CHECK (days >= 0),
  work_month      TEXT NOT NULL,
  note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_effort_project ON effort_records(project_id, category);

/* ---------------- 需求与演进链 ---------------- */

CREATE TABLE IF NOT EXISTS requirements (
  id                TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES projects(id),
  title             TEXT NOT NULL,
  proposer          TEXT NOT NULL,
  size              TEXT NOT NULL CHECK (size IN ('S', 'M', 'L')),
  status            TEXT NOT NULL,
  fields_completed  INTEGER NOT NULL CHECK (fields_completed BETWEEN 0 AND 8),
  created_at        TEXT NOT NULL
);

-- 一个需求 = 一条演进链（多次交付），不是一条状态记录
CREATE TABLE IF NOT EXISTS requirement_revisions (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  requirement_id      TEXT NOT NULL REFERENCES requirements(id),
  rev_no              INTEGER NOT NULL,
  description         TEXT NOT NULL,
  version_delivered   TEXT,
  est_days            REAL NOT NULL,
  act_days            REAL NOT NULL,
  cause_code          TEXT,                    -- C1-C8，首版为空
  delivered_at        TEXT,
  planned_phase       INTEGER NOT NULL DEFAULT 0,  -- 1=计划内分期，不计入返工
  UNIQUE (requirement_id, rev_no)
);

CREATE TABLE IF NOT EXISTS changes (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id),
  requirement_id  TEXT,
  title           TEXT NOT NULL,
  cause_code      TEXT NOT NULL,               -- C1-C8
  initiated_by    TEXT NOT NULL,               -- 客户 / 内部
  net_days        REAL NOT NULL,               -- 净增人天
  billing         TEXT NOT NULL,               -- 已计价 / 内部承担 / 待定
  status          TEXT NOT NULL,               -- 审批中 / 已通过 / 已关闭
  submitted_at    TEXT NOT NULL,
  assessed_at     TEXT,
  traced          INTEGER NOT NULL DEFAULT 1   -- 留痕完整（有需求关联+诱因+计价结论）
);

CREATE TABLE IF NOT EXISTS iterations (
  id               TEXT PRIMARY KEY,
  team             TEXT NOT NULL,
  committed_points REAL NOT NULL,
  done_points      REAL,
  status           TEXT NOT NULL,              -- 已完成 / 进行中
  note             TEXT,
  start_date       TEXT,
  end_date         TEXT
);

/* ---------------- 交付与运维 ---------------- */

CREATE TABLE IF NOT EXISTS deployments (
  id                TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES projects(id),
  client            TEXT NOT NULL,
  config_pack       TEXT NOT NULL,
  cycle_days        REAL NOT NULL,
  redo_count        INTEGER NOT NULL,
  survey_pass_rate  REAL NOT NULL,
  status            TEXT NOT NULL
);

-- category：A 不会用 / B 找不到 / C 理解错 / D 确实缺 / bug 报障
CREATE TABLE IF NOT EXISTS tickets (
  id                  TEXT PRIMARY KEY,
  client              TEXT NOT NULL,
  module              TEXT NOT NULL,
  category            TEXT NOT NULL CHECK (category IN ('A', 'B', 'C', 'D', 'bug')),
  created_at          TEXT NOT NULL,
  resolved_at         TEXT,
  first_response_min  REAL NOT NULL,
  is_repeat           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS module_usage (
  module      TEXT PRIMARY KEY,
  usage_rate  REAL NOT NULL,     -- 月活跃使用率 %
  satisfaction REAL NOT NULL      -- 5 分制
);

CREATE TABLE IF NOT EXISTS feedback_themes (
  id                TEXT PRIMARY KEY,
  theme             TEXT NOT NULL,
  count             INTEGER NOT NULL,
  type              TEXT NOT NULL,      -- 改进现有 / 新增能力 / 表达抱怨
  customer_count    INTEGER NOT NULL,
  contract_weight   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_periods (
  id            TEXT PRIMARY KEY,
  period        TEXT NOT NULL,
  total_count   INTEGER NOT NULL,
  nps           INTEGER NOT NULL,
  clustered_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_gaps (
  id            TEXT PRIMARY KEY,
  gap           TEXT NOT NULL,
  frequency     INTEGER NOT NULL,
  lost_amount   REAL NOT NULL,     -- 关联丢单（万元）
  cost_days     REAL NOT NULL,     -- 补齐成本（人天）
  strategy      TEXT NOT NULL,     -- 自研 / 合作 / 暂缓
  industry      TEXT,
  quarter       TEXT
);

CREATE TABLE IF NOT EXISTS estimate_baselines (
  id            TEXT PRIMARY KEY,
  dim           TEXT NOT NULL,      -- 交付模式 × 功能类型
  productivity  REAL,               -- 需求点 / 人天，NULL = 样本不足不出基线
  sample_n      INTEGER NOT NULL,
  updated_at    TEXT NOT NULL
);

/* ---------------- 知识资产 ---------------- */

CREATE TABLE IF NOT EXISTS knowledge_cards (
  id                TEXT PRIMARY KEY,
  requirement_id    TEXT,
  trigger_cond      TEXT NOT NULL,
  phenomenon        TEXT NOT NULL,
  root_cause        TEXT NOT NULL,
  action            TEXT NOT NULL,
  verify_criterion  TEXT NOT NULL,
  hit_count         INTEGER NOT NULL DEFAULT 0,
  adopt_count       INTEGER NOT NULL DEFAULT 0,
  effective_count   INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'active',   -- active / demoted / archived
  created_at        TEXT NOT NULL,
  last_recalled_at  TEXT
);

CREATE TABLE IF NOT EXISTS hypotheses (
  id            TEXT PRIMARY KEY,
  module        TEXT NOT NULL,
  claim         TEXT NOT NULL,
  evidence      TEXT NOT NULL,      -- JSON 数组
  falsify       TEXT NOT NULL,      -- JSON 数组
  method        TEXT NOT NULL,
  cost_days     REAL NOT NULL,
  strength      TEXT NOT NULL,
  status        TEXT NOT NULL,      -- 待验证 / 已验证 / 已否证 / 超期未回看
  created_at    TEXT NOT NULL,
  reviewed_at   TEXT,
  outcome       TEXT
);

CREATE TABLE IF NOT EXISTS version_candidates (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  module      TEXT NOT NULL,
  reach       REAL NOT NULL,
  impact      REAL NOT NULL,
  confidence  REAL NOT NULL,
  effort      REAL NOT NULL,
  risk        REAL NOT NULL,        -- 回归风险修正系数
  evidence    TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

/* ---------------- 问卷与配置 ---------------- */

CREATE TABLE IF NOT EXISTS survey_templates (
  id              TEXT PRIMARY KEY,
  client          TEXT NOT NULL,
  project_id      TEXT REFERENCES projects(id),
  version         TEXT NOT NULL,
  total_questions INTEGER NOT NULL,
  status          TEXT NOT NULL,      -- 草稿 / 已发放 / 已回答
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS survey_questions (
  template_id   TEXT NOT NULL REFERENCES survey_templates(id),
  code          TEXT NOT NULL,
  text          TEXT NOT NULL,
  qtype         TEXT NOT NULL,        -- single / multi / text
  options       TEXT NOT NULL,        -- JSON
  default_value TEXT,                 -- JSON
  order_no      INTEGER NOT NULL,
  section       TEXT,
  PRIMARY KEY (template_id, code)
);

CREATE TABLE IF NOT EXISTS survey_answers (
  template_id   TEXT NOT NULL REFERENCES survey_templates(id),
  code          TEXT NOT NULL,
  value         TEXT,                 -- JSON，NULL 表示"暂不确定"
  answered_at   TEXT NOT NULL,
  answered_by   TEXT,
  PRIMARY KEY (template_id, code)
);

/* ---------------- 口径与审计 ---------------- */

CREATE TABLE IF NOT EXISTS metrics_dict (
  name        TEXT PRIMARY KEY,
  category    TEXT NOT NULL,
  formula     TEXT NOT NULL,
  note        TEXT NOT NULL,
  source      TEXT NOT NULL,
  version     TEXT NOT NULL DEFAULT 'kpi_v1.1'
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  target      TEXT NOT NULL,
  payload     TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
  stage     TEXT PRIMARY KEY,
  count     INTEGER NOT NULL,
  order_no  INTEGER NOT NULL
);

-- 演示环境参数（团队规模、报价基准等），避免把公式常数写死在代码里
CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  note  TEXT
);

-- 模块的定性判断（一句话结论 + 模块边界），属于方法论资产而非业务数据
CREATE TABLE IF NOT EXISTS module_content (
  module     TEXT PRIMARY KEY,
  no         TEXT NOT NULL,
  name       TEXT NOT NULL,
  en         TEXT NOT NULL,
  tag        TEXT NOT NULL,
  judgment   TEXT NOT NULL,
  note       TEXT NOT NULL,
  order_no   INTEGER NOT NULL
);

-- 每周 EV / AC 快照：存原始挣值，CPI 在查询时相除得到
CREATE TABLE IF NOT EXISTS evm_snapshots (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  week   TEXT NOT NULL,
  ev     REAL NOT NULL,
  ac     REAL NOT NULL
);

-- ============================================================
-- 数据源治理（数据源设计 v1.0 第 8 章落地）
-- ============================================================

-- 数据源注册表：一条记录 = 一个可接入的具体源，而不是"一种接入方式"
CREATE TABLE IF NOT EXISTS data_sources (
  id            TEXT PRIMARY KEY,           -- S-MKT-API-01
  name          TEXT NOT NULL,
  type          TEXT NOT NULL,              -- api / mcp / excel / open
  module_key    TEXT NOT NULL,              -- market / estimate / ...
  role          TEXT NOT NULL,              -- primary / validate / supplement / reference
  vendor        TEXT,                       -- Jira / 金蝶 / NVD ...
  obj           TEXT NOT NULL,              -- 接入对象（表/接口/文件/网页）
  fields        TEXT NOT NULL,              -- 关键字段，逗号分隔
  trust         TEXT NOT NULL,              -- T1 / T2 / T3
  freq          TEXT NOT NULL,              -- realtime / hour / day / week / month / quarter / on_demand
  latency       TEXT NOT NULL,              -- 承诺延迟描述
  owner         TEXT NOT NULL,
  status        TEXT NOT NULL,              -- online / degraded / offline / manual / unconfigured
  adapter       TEXT,                       -- 适配器名（rest / mcp / excel / http）
  endpoint      TEXT,                       -- 真实探测地址（公开源必填）
  mcp_tool      TEXT,                       -- MCP 工具名（type=mcp 必填）
  join_key      TEXT,                       -- 去重/关联键
  fallback      TEXT,                       -- 断源降级方案
  dq_rules      TEXT,                       -- 数据质量规则
  note          TEXT,
  priority      INTEGER NOT NULL DEFAULT 50, -- 同指标多源时的裁决优先级（大者胜）
  last_sync     TEXT,
  records       INTEGER NOT NULL DEFAULT 0,
  error_rate    REAL NOT NULL DEFAULT 0,
  stale_days    INTEGER NOT NULL DEFAULT 0,
  order_no      INTEGER NOT NULL DEFAULT 0
);

-- 导入批次：Excel/CSV 每次导入一条，content_hash 保证幂等
CREATE TABLE IF NOT EXISTS ingest_batches (
  id            TEXT PRIMARY KEY,           -- BAT-20261018-001
  source_id     TEXT NOT NULL,
  filename      TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  rows_total    INTEGER NOT NULL,
  rows_ok       INTEGER NOT NULL,
  rows_bad      INTEGER NOT NULL,
  status        TEXT NOT NULL,              -- committed / dry_run / rejected / duplicate
  detail        TEXT,                       -- JSON：错误行明细
  created_at    TEXT NOT NULL,
  created_by    TEXT NOT NULL
);

-- 指标 → 源 的血缘：一个指标可以有 1 主源 + N 校验源
CREATE TABLE IF NOT EXISTS metric_sources (
  metric_id     TEXT NOT NULL,
  source_id     TEXT NOT NULL,
  role          TEXT NOT NULL,              -- primary / validate
  formula_part  TEXT,
  PRIMARY KEY (metric_id, source_id)
);

-- 源冲突事件：多源不一致时记录，不静默覆盖
CREATE TABLE IF NOT EXISTS source_conflicts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  metric_id   TEXT NOT NULL,
  source_a    TEXT NOT NULL,
  value_a     REAL NOT NULL,
  source_b    TEXT NOT NULL,
  value_b     REAL NOT NULL,
  resolved_by TEXT NOT NULL,
  note        TEXT NOT NULL,
  detected_at TEXT NOT NULL
);

-- ============================================================
-- 市场报价 Agent（推理链 + 规则权重 + 人工复核）
-- ============================================================
-- 设计原则与全站一致：只存原始事实（客户原话、历史成交、规则权重快照），
-- 推理链（trace）作为一次性运行证据整段留存，报价数字全部由引擎现算。
-- 不存"结论字段"，因为规则权重一调，所有报价都该跟着变。

-- 客户档案：报价的"熟悉度"与"折扣基线"都从这里来
CREATE TABLE IF NOT EXISTS customer_profiles (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  industry      TEXT NOT NULL,
  level         TEXT NOT NULL,             -- 战略 / 重点 / 普通 / 新客
  since         TEXT,
  deals         INTEGER NOT NULL,          -- 历史成交单数
  total_amount  REAL NOT NULL,             -- 累计合同额（万元）
  avg_discount  REAL NOT NULL,             -- 历史平均折扣（相对标准价，0.9 = 九折）
  on_time_rate  REAL,                      -- 按时付款率
  change_rate   REAL,                      -- 历史变更率
  note          TEXT
);

-- 商机 / 报价需求：客户原话是唯一事实来源，结构化字段是"从原话里抽出来的"
CREATE TABLE IF NOT EXISTS quote_requests (
  id            TEXT PRIMARY KEY,
  client_id     TEXT REFERENCES customer_profiles(id),
  client        TEXT NOT NULL,
  industry      TEXT,
  raw_text      TEXT NOT NULL,             -- 客户原话（口述/邮件/表单）
  budget_hint   TEXT,                      -- 客户口头预算
  deadline      TEXT,                      -- 期望上线时间
  channel       TEXT NOT NULL,             -- 客户经理提交 / 官网表单 / 邮件转述
  status        TEXT NOT NULL,             -- 待推理 / 待补充 / 已出价 / 已复核 / 已放弃
  created_at    TEXT NOT NULL
);

-- 历史报价库：向量检索的语料。scope 是当年那份需求描述，不是事后总结
CREATE TABLE IF NOT EXISTS quote_history (
  id          TEXT PRIMARY KEY,
  client      TEXT NOT NULL,
  industry    TEXT NOT NULL,
  title       TEXT NOT NULL,
  scope       TEXT NOT NULL,
  modules     TEXT NOT NULL,               -- 逗号分隔
  days        REAL NOT NULL,
  price       REAL NOT NULL,               -- 万元
  margin      REAL NOT NULL,               -- 实际毛利率
  won         INTEGER NOT NULL,            -- 是否中标
  quoted_at   TEXT NOT NULL
);

-- 行业经验库：同类案例的模块拆解模板（给"没有历史报价"的场景兜底）
CREATE TABLE IF NOT EXISTS case_templates (
  id          TEXT PRIMARY KEY,
  industry    TEXT NOT NULL,
  scenario    TEXT NOT NULL,
  name        TEXT NOT NULL,
  modules     TEXT NOT NULL,               -- JSON [{name,days,share}]
  days        REAL NOT NULL,
  tags        TEXT NOT NULL,
  win_rate    REAL,
  sample_n    INTEGER NOT NULL,
  note        TEXT
);

-- 定价规则与权重：这一张表就是"另一个页面"能改的全部东西
CREATE TABLE IF NOT EXISTS price_rules (
  code        TEXT PRIMARY KEY,            -- R-MKT-01 / R-CUS-01 / R-COST-01 / R-RTV-01
  group_key   TEXT NOT NULL,               -- market / customer / cost / retrieval / threshold
  name        TEXT NOT NULL,
  descr       TEXT NOT NULL,
  weight      REAL NOT NULL,               -- 融合权重（同组内归一化后使用）
  params      TEXT NOT NULL,               -- JSON：分位点、折扣阶梯、加成率、阈值
  enabled     INTEGER NOT NULL DEFAULT 1,
  editable    INTEGER NOT NULL DEFAULT 1,  -- 结构性规则不可编辑（如"利润率校验必须存在"）
  order_no    INTEGER NOT NULL DEFAULT 0
);

-- 规则版本：每次保存权重生成一个快照，报价单记录用的是哪个版本
CREATE TABLE IF NOT EXISTS price_rule_versions (
  version       TEXT PRIMARY KEY,          -- w_v1 / w_v2
  label         TEXT NOT NULL,
  snapshot      TEXT NOT NULL,             -- JSON 全量规则
  diff          TEXT,                      -- JSON 与上一版的差异
  created_at    TEXT NOT NULL,
  created_by    TEXT NOT NULL,
  active        INTEGER NOT NULL DEFAULT 0
);

-- 推理运行：trace 是给"推理过程可视化"用的整段证据，quote 是产物
CREATE TABLE IF NOT EXISTS quote_runs (
  id            TEXT PRIMARY KEY,          -- RUN-20261018-003
  request_id    TEXT NOT NULL REFERENCES quote_requests(id),
  rule_version  TEXT NOT NULL,
  status        TEXT NOT NULL,             -- clarified / quoted / below_margin / rejected / reviewed
  clarity       REAL NOT NULL,
  confidence    REAL NOT NULL,
  retune_rounds INTEGER NOT NULL DEFAULT 0,
  input_hash    TEXT NOT NULL,
  output_hash   TEXT,
  trace         TEXT NOT NULL,             -- JSON 全链路节点
  quote         TEXT,                      -- JSON 报价单
  answers       TEXT,                      -- JSON 追问补答，供影子试算复现同一输入
  created_at    TEXT NOT NULL,
  created_by    TEXT NOT NULL
);

-- 人工复核：AI 只出建议价，签字的是人
CREATE TABLE IF NOT EXISTS quote_reviews (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL REFERENCES quote_runs(id),
  decision    TEXT NOT NULL,               -- approved / adjusted / rejected
  reviewer    TEXT NOT NULL,
  final_price REAL,                        -- adjusted 时的最终价
  comment     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_request ON quote_runs(request_id, created_at);
CREATE INDEX IF NOT EXISTS idx_history_ind ON quote_history(industry);

-- ============================================================
-- 报价 Agent · 对话层与 LLM 审计（大模型接入）
-- ============================================================
-- 对话是流水线的正式输入源：客户经理的自由回答从这里进入，
-- 经槽位抽取后回写 answers，触发自动重跑。每句话都能回查到
-- 它触发了哪次推理（run_id），这是"从哪句话到哪个报价"的链条。

CREATE TABLE IF NOT EXISTS quote_dialogues (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id      TEXT NOT NULL REFERENCES quote_requests(id),
  turn_no         INTEGER NOT NULL,        -- 会话内第几轮（用户+AI 各占一行）
  role            TEXT NOT NULL,           -- user / agent
  content         TEXT NOT NULL,           -- 原文
  extracted_json  TEXT,                    -- 本轮槽位抽取结果 {slot:{value,confidence,evidence}}
  run_id          TEXT,                    -- 本回合触发的推理（可能为空）
  events_json     TEXT,                    -- agent 行：本回合完整 SSE 事件缓冲（断线补拉用）
  created_at      TEXT NOT NULL
);

-- LLM 调用审计：模型说了什么、花了多少 token、哪个版本、挂在哪个节点。
-- 没有这张表，接入大模型反而让"推理过程可溯源"变差——这是底线不是加分项。
CREATE TABLE IF NOT EXISTS llm_calls (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT,                       -- 关联的推理（对话阶段未触发推理时为空）
  turn_id      TEXT,                       -- 对话回合
  node         TEXT NOT NULL,              -- clarify / decompose / review / explain
  purpose      TEXT NOT NULL,              -- ask / extract / explain / constraint
  model        TEXT NOT NULL,              -- 模型名或 offline
  prompt_hash  TEXT NOT NULL,              -- prompt 指纹（全文不落库，防泄密可后补）
  tokens_in    INTEGER,
  tokens_out   INTEGER,
  latency_ms   REAL,
  status       TEXT NOT NULL,              -- ok / schema_fail / offline / timeout / error
  created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dialogues_req ON quote_dialogues(request_id, turn_no);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(run_id);

/* ---------------- 问卷草稿（"仅保存" 不触发推导） ---------------- */

-- 待确认事项里改的答案与人工确认值，先存这里，等实施确认无误再统一计算。
-- 与 survey_answers 分开：那张表是"已确认并参与推导"的答案，这张是草稿。
CREATE TABLE IF NOT EXISTS config_drafts (
  template_id  TEXT PRIMARY KEY,
  answers      TEXT NOT NULL,          -- JSON：问卷答案
  overrides    TEXT NOT NULL,          -- JSON：人工确认的配置值 {C65:{v,why,at}}
  saved_at     TEXT NOT NULL,
  saved_by     TEXT
);

/* ---------------- 大模型配置（页面可改，不用再改环境变量） ---------------- */

-- 单行配置：本地单机演示，api_key 明文存本机 sqlite；
-- 接口对外只回脱敏串，绝不回明文。
CREATE TABLE IF NOT EXISTS llm_configs (
  id           TEXT PRIMARY KEY,      -- 固定 'default'
  provider     TEXT NOT NULL,         -- deepseek / openai / custom
  base_url     TEXT NOT NULL,
  model        TEXT NOT NULL,
  api_key      TEXT NOT NULL,
  temperature  REAL NOT NULL DEFAULT 0.2,
  updated_at   TEXT NOT NULL,
  updated_by   TEXT
);
