-- 合成 DDL/规则文本片段（ontology_token_pilot 样本③，文件用途见 README.md）
-- 说明：本文件不是 JSON，fixture 测试不要求 json.loads；
--       由材料解析器按 ddl/text 类事实处理，定位器形态见 文档/接口文档/08 §1.3
--       （{kind:'ddl',file,line,table,column}）。
-- 全部内容为确定性合成示例，不含真实业务数据、真实域名、密钥或个人信息。

CREATE TABLE t_syn_dispatch_log (
    id           BIGINT       NOT NULL,
    device_no    VARCHAR(64)  NOT NULL,
    event_code   VARCHAR(32)  NOT NULL,
    occurred_at  DATETIME     NOT NULL,
    detail       VARCHAR(500),
    PRIMARY KEY (id)
);

-- rule R-SYN-101 低电量充电闭锁
-- 触发条件：站级 SOC 低于 10% 且电网侧无放电指令
-- 执行内容：闭锁全部充电通道，等待运行人员复核
-- 归属对象：battery_ess（站级电池储能单元，金样键 rule.low_soc_charge_lock）
-- rule-end

-- action A-SYN-07 值班通知
-- 触发时机：rule R-SYN-101 进入闭锁时
-- 执行效果：向当值运行人员推送低电量充电闭锁告警并要求确认（金样键 action.notify_duty_shift）
-- action-end
