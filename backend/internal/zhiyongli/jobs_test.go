package zhiyongli

import (
	"testing"
	"time"
)

func TestJobWindows(t *testing.T) {
	now := time.Date(2026, 11, 9, 0, 0, 0, 0, time.FixedZone("CST", 8*3600))
	for _, v := range []struct {
		kind, start, end string
		valid            bool
	}{
		{"week", "2026-09-07", "2026-09-14", true},
		{"week", "2026-11-02", "2026-11-09", true},
		{"week", "2026-11-09", "", false},
		{"week", "2026-09-08", "", false},
		{"month", "2026-09-01", "2026-10-01", true},
		{"month", "2026-11-01", "", false},
		{"month", "2026-09-02", "", false},
		{"day", "2026-09-07", "", false},
	} {
		got, err := jobWindow(v.kind, v.start, now)
		if (err == nil) != v.valid || v.valid && got != v.end {
			t.Errorf("%+v got %q / %v", v, got, err)
		}
	}
	// 一键周评审：工作日内取上一自然周；周末封口后取刚结束的一周；封口前仍取上一周。
	for _, v := range []struct {
		at   time.Time
		want string
	}{
		{time.Date(2026, 11, 9, 10, 0, 0, 0, now.Location()), "2026-11-02"},
		{time.Date(2026, 11, 6, 23, 0, 0, 0, now.Location()), "2026-10-26"},
		{time.Date(2026, 11, 7, 1, 59, 0, 0, now.Location()), "2026-10-26"},
		{time.Date(2026, 11, 7, 2, 0, 0, 0, now.Location()), "2026-11-02"},
		{time.Date(2026, 11, 8, 20, 0, 0, 0, now.Location()), "2026-11-02"},
	} {
		if got := lastClosedWorkweek(v.at); got != v.want {
			t.Errorf("lastClosedWorkweek(%s)=%s want %s", v.at.Format(time.RFC3339), got, v.want)
		}
	}
	// 上一周全为假期时不改评更早的周，也不生成评分。
	holiday := time.Date(2026, 2, 23, 10, 0, 0, 0, now.Location())
	if start := lastClosedWorkweek(holiday); start != "2026-02-16" {
		t.Fatal("holiday week must stay the target", start)
	} else if _, err := readyWorkdayWindow("week", start, holiday); err == nil {
		t.Fatal("holiday week must not create scores")
	}
	// 国庆前该周最后工作日为9/30；10/1 02:00已封口，无需等待非工作日结束。
	if _, err := jobWindow("month", "2026-09-01", time.Date(2026, 10, 1, 12, 0, 0, 0, now.Location())); err != nil {
		t.Fatal("month workdays are closed", err)
	}
}
