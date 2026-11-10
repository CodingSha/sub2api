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
	// 国庆前该周最后工作日为9/30；10/1 02:00已封口，无需等待非工作日结束。
	if _, err := jobWindow("month", "2026-09-01", time.Date(2026, 10, 1, 12, 0, 0, 0, now.Location())); err != nil {
		t.Fatal("month workdays are closed", err)
	}
}
