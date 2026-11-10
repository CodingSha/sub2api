package zhiyongli

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"time"
)

const WorkdayScopeVersion = "workday-scope-20261109.1"

//go:embed workday-registry.json
var defaultWorkdayRegistry []byte

type WorkdayScope struct {
	Version        string   `json:"version"`
	RegistrySHA    string   `json:"registry_sha256"`
	CalendarSHA    string   `json:"calendar_sha256"`
	PeriodStart    string   `json:"period_start"`
	PeriodEnd      string   `json:"period_end"`
	WorkingDates   []string `json:"working_dates"`
	ObservedUntil  string   `json:"observed_until"`
	FreezeAt       string   `json:"freeze_at"`
	CandidateDueAt *string  `json:"candidate_due_at"`
}
type workdayPlan struct {
	CalendarSHA    string   `json:"calendar_sha256"`
	WorkingDates   []string `json:"working_dates"`
	FreezeAt       *string  `json:"freeze_at"`
	CandidateDueAt *string  `json:"candidate_due_at"`
}
type workdayRegistry struct {
	Version     string                 `json:"version"`
	CalendarSHA string                 `json:"calendar_sha256"`
	Weeks       map[string]workdayPlan `json:"weeks"`
}

func bytesSHA(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

func loadWorkdayRegistry(expected string) (workdayRegistry, string, error) {
	raw := defaultWorkdayRegistry
	if path := os.Getenv("ZHIYONGLI_WORKDAY_REGISTRY"); path != "" {
		var err error
		raw, err = os.ReadFile(path)
		if err != nil {
			return workdayRegistry{}, "", invalid("无法读取已配置工作日范围登记")
		}
	}
	if expected != "" && bytesSHA(raw) != expected {
		if !hashValid(expected) {
			return workdayRegistry{}, "", invalid("日历范围签名无效")
		}
		if bytesSHA(defaultWorkdayRegistry) == expected {
			raw = defaultWorkdayRegistry
		} else {
			dir := os.Getenv("ZHIYONGLI_WORKDAY_REGISTRY_ARCHIVE")
			if dir == "" {
				return workdayRegistry{}, "", invalid("冻结日历范围未在受信登记或归档中")
			}
			var err error
			raw, err = os.ReadFile(filepath.Join(dir, expected+".json"))
			if err != nil || bytesSHA(raw) != expected {
				return workdayRegistry{}, "", invalid("归档日历范围缺失或签名损坏")
			}
		}
	}
	var r workdayRegistry
	if json.Unmarshal(raw, &r) != nil || r.Version != "workday-registry-20261109.1" || !hashValid(r.CalendarSHA) {
		return r, "", invalid("日历范围登记版本或来源签名无效")
	}
	return r, bytesSHA(raw), nil
}

func workdayWindow(start, end, expected string) (*WorkdayScope, error) {
	r, sha, err := loadWorkdayRegistry(expected)
	if err != nil {
		return nil, err
	}
	tz := time.FixedZone("CST", 8*3600)
	a, err := time.ParseInLocation("2006-01-02", start, tz)
	if err != nil {
		return nil, invalid("工作日范围开始日期无效")
	}
	b, err := time.ParseInLocation("2006-01-02", end, tz)
	if err != nil || !a.Before(b) {
		return nil, invalid("工作日范围起止无效")
	}
	s := &WorkdayScope{Version: WorkdayScopeVersion, RegistrySHA: sha, CalendarSHA: r.CalendarSHA,
		PeriodStart: start, PeriodEnd: end, WorkingDates: []string{}}
	var freeze, due time.Time
	for cursor := a.AddDate(0, 0, -(int(a.Weekday())+6)%7); cursor.Before(b); cursor = cursor.AddDate(0, 0, 7) {
		p, ok := r.Weeks[cursor.Format("2006-01-02")]
		if !ok || p.CalendarSHA != r.CalendarSHA {
			return nil, invalid("评价范围缺少已核年份或跨年日历计划")
		}
		for _, d := range p.WorkingDates {
			if d >= start && d < end {
				s.WorkingDates = append(s.WorkingDates, d)
			}
		}
		f := cursor.AddDate(0, 0, 7).Add(2 * time.Hour)
		if p.FreezeAt != nil {
			f, err = time.Parse(time.RFC3339, *p.FreezeAt)
			if err != nil {
				return nil, invalid("日历封口时刻无效")
			}
		}
		if f.After(freeze) {
			freeze = f
		}
		if p.CandidateDueAt != nil {
			d, e := time.Parse(time.RFC3339, *p.CandidateDueAt)
			if e != nil {
				return nil, invalid("日历候选截止时刻无效")
			}
			if d.After(due) {
				due = d
			}
		}
	}
	cutoff := b
	if len(s.WorkingDates) > 0 {
		last, e := time.ParseInLocation("2006-01-02", s.WorkingDates[len(s.WorkingDates)-1], tz)
		if e != nil {
			return nil, invalid("工作日期无效")
		}
		cutoff = last.AddDate(0, 0, 1)
	}
	if a.Day() == 1 && b.Equal(a.AddDate(0, 1, 0)) && b.Add(2*time.Hour).After(freeze) {
		freeze = b.Add(2 * time.Hour)
	}
	s.ObservedUntil, s.FreezeAt = cutoff.Format(time.RFC3339), freeze.Format(time.RFC3339)
	if !due.IsZero() {
		text := due.Format(time.RFC3339)
		s.CandidateDueAt = &text
	}
	return s, nil
}

func (s *WorkdayScope) validate(start, end string) error {
	if s == nil || s.Version != WorkdayScopeVersion || s.PeriodStart != start || s.PeriodEnd != end {
		return invalid("缺少当前工作日范围合同")
	}
	want, err := workdayWindow(start, end, s.RegistrySHA)
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(s, want) {
		return invalid("计分范围与冻结工作日历不一致")
	}
	return nil
}

func (s *WorkdayScope) contains(t time.Time) bool {
	d := t.In(time.FixedZone("CST", 8*3600)).Format("2006-01-02")
	for _, day := range s.WorkingDates {
		if day == d {
			return true
		}
	}
	return false
}
