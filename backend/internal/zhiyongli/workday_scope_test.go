package zhiyongli

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestSelectedWorkdaysPreservedAndBounded(t *testing.T) {
	var registry map[string]any
	if err := json.Unmarshal(defaultWorkdayRegistry, &registry); err != nil {
		t.Fatal(err)
	}
	plan := registry["weeks"].(map[string]any)["2026-11-02"].(map[string]any)
	path := filepath.Join(t.TempDir(), "registry.json")
	t.Setenv("ZHIYONGLI_WORKDAY_REGISTRY", path)
	for _, tc := range []struct {
		name  string
		dates any
		valid bool
	}{
		{"三日", []string{"2026-11-04", "2026-11-05", "2026-11-06"}, true},
		{"空集", []string{}, false}, {"null", nil, false},
		{"重复", []string{"2026-11-04", "2026-11-04"}, false},
		{"倒序", []string{"2026-11-05", "2026-11-04"}, false},
		{"周末", []string{"2026-11-07"}, false},
		{"整周", []string{"2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			plan["evaluation_dates"] = tc.dates
			raw, err := json.Marshal(registry)
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(path, raw, 0600); err != nil {
				t.Fatal(err)
			}
			s, err := workdayWindow("2026-11-02", "2026-11-09", "")
			if (err == nil) != tc.valid {
				t.Fatalf("范围有效性不符: %v", err)
			}
			if !tc.valid {
				return
			}
			if !reflect.DeepEqual(s.EvaluationDates, tc.dates) {
				t.Fatal("局部范围被丢弃")
			}
			tz := time.FixedZone("CST", 8*3600)
			if s.contains(time.Date(2026, 11, 2, 12, 0, 0, 0, tz)) || !s.contains(time.Date(2026, 11, 4, 12, 0, 0, 0, tz)) {
				t.Fatal("局部范围外行为进入计分")
			}
			if err = s.validate(s.PeriodStart, s.PeriodEnd); err != nil {
				t.Fatal(err)
			}
			s.EvaluationDates = nil
			if s.validate(s.PeriodStart, s.PeriodEnd) == nil {
				t.Fatal("删除三日范围后仍被接受")
			}
			if _, err = workdayWindow("2026-11-01", "2026-12-01", ""); err == nil {
				t.Fatal("局部周不能冒充月评来源")
			}
		})
	}
}

func TestWorkdayWindowsCloseBeforeNaturalWeekAndRespectMakeup(t *testing.T) {
	tz := time.FixedZone("CST", 8*3600)
	for _, v := range []struct {
		start string
		at    time.Time
		valid bool
	}{
		{"2026-11-02", time.Date(2026, 11, 7, 1, 59, 59, 0, tz), false},
		{"2026-11-02", time.Date(2026, 11, 7, 2, 0, 0, 0, tz), true},
		{"2026-09-14", time.Date(2026, 9, 20, 12, 0, 0, 0, tz), false},
		{"2026-09-14", time.Date(2026, 9, 21, 2, 0, 0, 0, tz), true},
		{"2026-02-16", time.Date(2026, 3, 1, 0, 0, 0, 0, tz), false},
		{"2026-12-28", time.Date(2027, 1, 10, 0, 0, 0, 0, tz), false},
	} {
		_, err := jobWindow("week", v.start, v.at)
		if (err == nil) != v.valid {
			t.Fatalf("%s %v: %v", v.start, v.at, err)
		}
	}
	s, err := workdayWindow("2026-09-14", "2026-09-21", "")
	if err != nil {
		t.Fatal(err)
	}
	if s.contains(time.Date(2026, 9, 19, 12, 0, 0, 0, tz)) || !s.contains(time.Date(2026, 9, 20, 12, 0, 0, 0, tz)) {
		t.Fatal("makeup dates must use official calendar")
	}
	if err = s.validate(s.PeriodStart, s.PeriodEnd); err != nil {
		t.Fatal(err)
	}
	s.WorkingDates = append(s.WorkingDates, "2026-09-19")
	if s.validate(s.PeriodStart, s.PeriodEnd) == nil {
		t.Fatal("tampered dates accepted")
	}
}

func TestReportRejectsWeekendEvidenceAndMixedScope(t *testing.T) {
	r := withObservation(sampleResult(), 1, []string{"2026-09-13"})
	r.PolicyVersions.Rules = RulesVersion
	r.PolicyVersions.Review = ReviewPolicyVersion
	r.PolicyVersions.Scope = WorkdayScopeVersion
	r.Eligibility.Version = CurrentEligibilityVersion
	r.WorkdayScope, _ = workdayWindow(r.PeriodStart, r.PeriodEnd, "")
	if r.Validate() == nil {
		t.Fatal("weekend person evidence accepted")
	}
	m := qualifiedMonth(3)
	m.WorkdayScope, _ = workdayWindow(m.PeriodStart, m.PeriodEnd, "")
	m.PolicyVersions.Scope = WorkdayScopeVersion
	if m.Validate() == nil {
		t.Fatal("mixed natural and workday weeks accepted")
	}
}
