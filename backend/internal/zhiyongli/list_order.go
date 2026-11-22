package zhiyongli

// reportOrder 只接受登记的排序键，先对完整查询结果排序再分页；缺失评分始终排最后。
func reportOrder(f Filter) (string, error) {
	orders := map[string]string{
		"": "r.period_start DESC,r.id DESC", "period_desc": "r.period_start DESC,r.id DESC",
		"period_asc": "r.period_start ASC,r.id ASC",
		"email_asc":  "r.body->>'user_email' ASC,r.period_start DESC,r.id DESC",
		"email_desc": "r.body->>'user_email' DESC,r.period_start DESC,r.id DESC",
	}
	if order, ok := orders[f.Sort]; ok {
		return order, nil
	}
	for _, direction := range []string{"asc", "desc"} {
		dir := "ASC"
		if direction == "desc" {
			dir = "DESC"
		}
		if f.Sort == "score_"+direction && f.PeriodType != "day" {
			return "NULLIF(r.body->>'score','')::numeric " + dir + " NULLS LAST,r.period_start DESC,r.id DESC", nil
		}
		for _, code := range []string{"P", "M", "J", "E"} {
			if f.Sort == code+"_"+direction {
				return "(SELECT NULLIF(d->>'score','')::numeric FROM jsonb_array_elements(COALESCE(NULLIF(r.body->'dimensions','null'::jsonb),'[]'::jsonb)) d WHERE d->>'code'='" + code + "') " + dir + " NULLS LAST,r.period_start DESC,r.id DESC", nil
			}
		}
	}
	return "", invalid("报告排序参数无效")
}

func jobOrder(sort string) (string, error) {
	orders := map[string]string{
		"":             jobActive + " DESC, CASE j.state WHEN 'running' THEN 0 WHEN 'needs_review' THEN 1 WHEN 'unknown' THEN 1 WHEN 'failed' THEN 1 WHEN 'queued' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END, j.id DESC",
		"updated_desc": "j.updated_at DESC,j.id DESC", "updated_asc": "j.updated_at ASC,j.id ASC",
		"period_desc": "j.period_start DESC,j.id DESC", "period_asc": "j.period_start ASC,j.id ASC",
		"email_asc": "u.email ASC,j.id DESC", "email_desc": "u.email DESC,j.id DESC",
	}
	if order, ok := orders[sort]; ok {
		return order, nil
	}
	return "", invalid("评审排序参数无效")
}
