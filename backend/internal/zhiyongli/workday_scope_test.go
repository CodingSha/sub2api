package zhiyongli

import (
	"testing"
	"time"
)

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
