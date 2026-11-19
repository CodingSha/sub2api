package zhiyongli

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

const TaskReviewVersion = "task-evidence-20261106.1"
const SampleReviewVersion = "sample-evidence-20261115.1"
const SourceReviewVersion = "source-evidence-20261116.1"

var ErrTaskCaseForbidden = errors.New("该账户不提供个人任务解读")

// TaskRecord 是已脱敏的任务过程记录，不包含审计请求体或系统提示词。
type TaskRecord struct {
	ID          string     `json:"id"`
	SourceHash  string     `json:"source_hash"`
	TextHash    string     `json:"text_hash"`
	OccurredAt  *time.Time `json:"occurred_at"`
	Role        string     `json:"role"`
	Origin      string     `json:"origin"`
	Text        string     `json:"text"`
	Limitations []string   `json:"limitations"`
}
type TaskClaim struct {
	Text        string           `json:"text"`
	ItemCodes   []string         `json:"item_codes"`
	RecordIDs   []string         `json:"record_ids"`
	Limitations []string         `json:"limitations"`
	Assessment  *ClaimAssessment `json:"assessment,omitempty"`
}

// ClaimAssessment 解释该段材料的评价作用，不新增分数或改变分档计算。
type ClaimAssessment struct {
	Kind   string `json:"kind"`
	Reason string `json:"reason"`
}
type TaskCase struct {
	ID            string       `json:"id"`
	TaskKey       string       `json:"task_key"`
	Title         string       `json:"title"`
	Goal          string       `json:"goal"`
	Outcome       string       `json:"outcome"`
	ContextStatus string       `json:"context_status"`
	RecordCount   int          `json:"record_count"`
	Claims        []TaskClaim  `json:"claims"`
	Limitations   []string     `json:"limitations"`
	Records       []TaskRecord `json:"records,omitempty"`
}
type WeekJudgment struct {
	ItemCode    string   `json:"item_code"`
	Reason      string   `json:"reason"`
	TaskKeys    []string `json:"task_keys"`
	CaseIDs     []string `json:"case_ids"`
	Limitations []string `json:"limitations"`
}
type TaskReview struct {
	Version         string         `json:"version"`
	Status          string         `json:"status"`
	TaskKeys        []string       `json:"task_keys"`
	CheckedTaskKeys []string       `json:"checked_task_keys"`
	ScopeNote       string         `json:"scope_note"`
	Cases           []TaskCase     `json:"cases"`
	WeekJudgments   []WeekJudgment `json:"week_judgments"`
}

func taskText(s string, limit int) bool {
	return strings.TrimSpace(s) != "" && len(s) <= limit && !credentialPattern.MatchString(s)
}
func stringSet(values []string) (map[string]bool, bool) {
	out := map[string]bool{}
	for _, s := range values {
		if !sourceRefPattern.MatchString(s) || out[s] {
			return nil, false
		}
		out[s] = true
	}
	return out, true
}
func (r *Result) validateTaskReview() error {
	t := r.TaskReview
	if t == nil {
		return nil
	}
	initial := t.Status == "initial" && t.Version == SampleReviewVersion && r.SampleInitial != nil
	linked := t.Status == "source_linked" && t.Version == SourceReviewVersion
	unreviewed := initial || linked
	generated := t.Status == "generated"
	if r.PeriodType != "week" || !unreviewed && (t.Version != TaskReviewVersion || (t.Status != "case_checked" && t.Status != "week_checked" && !generated)) || !taskText(t.ScopeNote, 6000) {
		return invalid("任务依据版本、范围或核查状态无效")
	}
	keys, ok := stringSet(t.TaskKeys)
	if !ok || len(keys) == 0 || len(keys) > 10000 {
		return invalid("任务范围标识重复或无效")
	}
	checked, ok := stringSet(t.CheckedTaskKeys)
	if !ok || !unreviewed && !generated && len(checked) == 0 || (unreviewed || generated) && len(checked) != 0 {
		return invalid("缺少明确的已核任务范围")
	}
	for k := range checked {
		if !keys[k] {
			return invalid("已核任务不在本周范围内")
		}
	}
	if r.Eligibility != nil && !unreviewed {
		observed := map[string]bool{}
		for _, e := range r.Eligibility.Observations {
			for _, k := range e.TaskKeys {
				observed[k] = true
			}
		}
		if len(observed) != len(keys) {
			return invalid("任务依据与周资格任务集合不一致")
		}
		for k := range observed {
			if !keys[k] {
				return invalid("任务依据与周资格任务集合不一致")
			}
		}
	}
	if len(t.Cases) == 0 || len(t.Cases) > 12 {
		return invalid("代表案例须为1至12个")
	}
	ids := map[string]bool{}
	caseKeys := map[string]bool{}
	observations := map[string]Observation{}
	if r.Eligibility != nil {
		for _, e := range r.Eligibility.Observations {
			observations[e.SourceRef] = e
		}
	}
	start, _ := date(r.PeriodStart)
	start = start.Add(-8 * time.Hour)
	end, _ := date(r.PeriodEnd)
	end = end.Add(-8 * time.Hour)
	for _, c := range t.Cases {
		if !hashValid(c.ID) || ids[c.ID] || (!(unreviewed || generated) && !checked[c.TaskKey] || (unreviewed || generated) && !keys[c.TaskKey]) || caseKeys[c.TaskKey] || !taskText(c.Title, 600) || !taskText(c.Goal, 6000) || !taskText(c.Outcome, 6000) || len(c.Limitations) == 0 || (c.ContextStatus != "available_records" && c.ContextStatus != "source_incomplete") {
			return invalid("任务案例缺少明确身份、目标、结果或资料限制")
		}
		ids[c.ID], caseKeys[c.TaskKey] = true, true
		limit := 1000
		if initial {
			// 完整抽样来源组可含千条以上事件；保留正文总容量限制及分页传输。
			limit = 5000
		}
		if c.RecordCount != len(c.Records) || c.RecordCount < 1 || c.RecordCount > limit {
			return invalid("任务案例记录数不符或超限")
		}
		raw, _ := json.Marshal(c)
		if len(raw) > 2<<20 || credentialPattern.Match(raw) {
			return invalid("任务案例过大或含未脱敏凭证")
		}
		records := map[string]bool{}
		var last time.Time
		for _, e := range c.Records {
			if !sourceRefPattern.MatchString(e.ID) || records[e.ID] || !hashValid(e.SourceHash) || !hashValid(e.TextHash) || len(e.Text) > 256<<10 || credentialPattern.MatchString(e.Text) || strings.TrimSpace(e.Text) == "" && len(e.Limitations) == 0 {
				return invalid("任务记录标识、原文或哈希无效")
			}
			h := sha256.Sum256([]byte(e.Text))
			if hex.EncodeToString(h[:]) != e.TextHash {
				return invalid("展示记录哈希不一致")
			}
			switch e.Role {
			case "user", "assistant", "tool_call", "tool_result", "context":
			default:
				return invalid("任务记录角色无效")
			}
			switch e.Origin {
			case "direct_expression", "mixed_material", "assistant_output", "recorded_tool", "unattributed":
			default:
				return invalid("任务记录归属无效")
			}
			if e.Role == "user" && e.Origin != "direct_expression" && e.Origin != "mixed_material" || e.Role == "assistant" && e.Origin != "assistant_output" || (e.Role == "tool_call" || e.Role == "tool_result") && e.Origin != "recorded_tool" {
				return invalid("任务记录角色与归属冲突")
			}
			if e.OccurredAt == nil {
				if len(e.Limitations) == 0 || c.ContextStatus != "source_incomplete" {
					return invalid("未知时间必须披露资料不完整")
				}
			} else {
				if !e.OccurredAt.Before(end) || e.OccurredAt.Before(last) {
					return invalid("任务记录跨评价截止时间或时序错误")
				}
				last = *e.OccurredAt
				if e.OccurredAt.Before(start) && len(e.Limitations) == 0 {
					return invalid("历史任务背景必须注明不能计为本周表现")
				}
				if r.Eligibility != nil && !unreviewed && e.Role == "user" && !e.OccurredAt.Before(start) {
					ob, exists := observations[e.ID]
					belongs := false
					for _, k := range ob.TaskKeys {
						belongs = belongs || k == c.TaskKey
					}
					if !exists || !belongs || ob.SourceHash != e.SourceHash || !ob.OccurredAt.Equal(*e.OccurredAt) {
						return invalid("本人任务记录与周资格来源不符")
					}
				}
			}
			records[e.ID] = true
		}
		if len(c.Claims) == 0 || len(c.Claims) > 24 {
			return invalid("案例须有可追溯的综合分析")
		}
		for _, claim := range c.Claims {
			if unreviewed && (len(claim.ItemCodes) == 0 || claim.Assessment != nil) {
				return invalid("来源说明仅复用有据分项理由，不得附加未经复查的评价结论")
			}
			if !taskText(claim.Text, 6000) || len(claim.RecordIDs) == 0 {
				return invalid("案例判断缺少理由或原文")
			}
			if a := claim.Assessment; a != nil {
				if !taskText(a.Reason, 2400) || (a.Kind != "support" && a.Kind != "shortfall" && a.Kind != "unresolved") {
					return invalid("案例评价作用或说明无效")
				}
				if a.Kind != "unresolved" && len(claim.ItemCodes) == 0 {
					return invalid("评分支持或表现不足须明确对应子项")
				}
			}
			refs, ok := stringSet(claim.RecordIDs)
			if !ok {
				return invalid("案例判断引用重复")
			}
			for ref := range refs {
				if !records[ref] {
					return invalid("案例判断指向任务之外的记录")
				}
			}
			for _, code := range claim.ItemCodes {
				if _, ok := itemNames[code]; !ok {
					return invalid("案例子项不存在")
				}
				if unreviewed {
					matched := false
					for _, item := range r.Items {
						if item.Code != code || item.Reason != claim.Text {
							continue
						}
						if item.State != "observed" && len(item.Evidence) == 0 {
							// 无分项仅关联原语境，不将记录当作缺失表现的正反证。
							for _, record := range c.Records {
								matched = matched || refs[record.ID] && record.Role == "user"
							}
						}
						for _, evidence := range item.Evidence {
							for _, record := range c.Records {
								if refs[record.ID] && evidence.SourceRef == record.ID && evidence.SourceHash == record.SourceHash && record.OccurredAt != nil && evidence.OccurredAt.Equal(*record.OccurredAt) && strings.Contains(record.Text, evidence.Quote) {
									matched = true
									break
								}
							}
						}
					}
					if !matched {
						return invalid("来源说明须沿用原分项理由并对应实际本人引文")
					}
				}
			}
		}
	}
	if generated {
		if len(checked) != 0 || len(t.WeekJudgments) != 0 {
			return invalid("同次生成的任务解读不得声明另次核查完成")
		}
		return nil
	}
	if t.Status == "case_checked" || unreviewed {
		if len(t.WeekJudgments) != 0 {
			return invalid("案例核查不得冒充整周判断")
		}
		return nil
	}
	if len(checked) != len(keys) || len(t.WeekJudgments) != 12 {
		return invalid("整周核查须覆盖全部任务并说明十二项判断")
	}
	codes := map[string]bool{}
	for _, j := range t.WeekJudgments {
		if _, ok := itemNames[j.ItemCode]; !ok || codes[j.ItemCode] || !taskText(j.Reason, 6000) || len(j.TaskKeys) == 0 {
			return invalid("整周子项判断无效")
		}
		codes[j.ItemCode] = true
		for _, k := range j.TaskKeys {
			if !checked[k] {
				return invalid("整周判断引用未核任务")
			}
		}
		for _, id := range j.CaseIDs {
			if !ids[id] {
				return invalid("整周判断案例不存在")
			}
		}
	}
	return nil
}

// TaskCasePage 按需返回一个案例的记录；分页只改变传输，不丢弃来源。
type TaskCasePage struct {
	TaskCase
	Records  []TaskRecord `json:"records"`
	Page     int          `json:"page"`
	PageSize int          `json:"page_size"`
	Total    int          `json:"total"`
}

// 只传回案例元数据及本页原文；记录顺序由原数组位置明确保持。
const taskCasePageQuery = `SELECT c.body - 'records', jsonb_array_length(c.body->'records'),
    COALESCE((SELECT jsonb_agg(e.value ORDER BY e.ordinality)
        FROM jsonb_array_elements(c.body->'records') WITH ORDINALITY AS e(value, ordinality)
        WHERE e.ordinality > $3 AND e.ordinality <= $4), '[]'::jsonb)
    FROM zhiyongli_task_cases c JOIN zhiyongli_reports r ON r.id=c.report_id
    WHERE r.id=$1 AND c.case_key=$2`

func (s *Store) GetTaskCase(ctx context.Context, reportID, owner int64, caseID string, page, size int) (TaskCasePage, error) {
	var out TaskCasePage
	if !hashValid(caseID) || page < 1 || page > 100000 || size < 1 || size > 50 {
		return out, invalid("任务案例或分页参数无效")
	}
	if owner > 0 {
		if err := personalUserAllowed(owner); err != nil {
			return out, ErrTaskCaseForbidden
		}
		var whitelisted bool
		if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM audit_whitelist_users WHERE user_id=$1)`, owner).Scan(&whitelisted); err != nil {
			return out, err
		}
		if whitelisted {
			return out, ErrTaskCaseForbidden
		}
	}
	// 单条查询同时核验修订、发布状态与本人权限，避免先查权限再读原文的竞态。
	query := taskCasePageQuery
	start := (page - 1) * size
	args := []any{reportID, caseID, start, start + size}
	if owner > 0 {
		query += " AND r.user_id=$5 AND r.publication_status='published' AND NOT EXISTS(SELECT 1 FROM audit_whitelist_users WHERE user_id=$5)"
		args = append(args, owner)
	}
	var raw, records []byte
	err := s.db.QueryRowContext(ctx, query, args...).Scan(&raw, &out.Total, &records)
	if errors.Is(err, sql.ErrNoRows) {
		return out, ErrNotFound
	}
	if err != nil {
		return out, err
	}
	if err = json.Unmarshal(raw, &out.TaskCase); err != nil {
		return out, err
	}
	if err = json.Unmarshal(records, &out.Records); err != nil {
		return out, err
	}
	out.Page, out.PageSize = page, size
	return out, nil
}
