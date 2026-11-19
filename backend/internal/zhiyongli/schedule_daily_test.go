package zhiyongli

import (
	"testing"
	"time"
)

func TestPreparationDayStartsAt23AndSurvivesMidnight(t *testing.T) {
	for _, test := range []struct{ at, want string }{
		{"2026-11-17T22:59:59+08:00", "2026-11-16"},
		{"2026-11-17T23:00:00+08:00", "2026-11-17"},
		{"2026-11-18T00:00:00+08:00", "2026-11-17"},
		{"2026-11-18T07:59:59+08:00", "2026-11-17"},
		{"2026-11-17T15:00:00Z", "2026-11-17"},
	} {
		at, err := time.Parse(time.RFC3339, test.at)
		if err != nil {
			t.Fatal(err)
		}
		if got := preparationDay(at).Format("2006-01-02"); got != test.want {
			t.Errorf("%s: got %s, want %s", test.at, got, test.want)
		}
	}
}

func TestDailyCompensationSurvivesWeekFreezeAndMondayMorning(t *testing.T) {
	scope := &WorkdayScope{PeriodStart: "2026-11-16", PeriodEnd: "2026-11-23", WorkingDates: []string{"2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"}}
	for _, test := range []struct{ at, want string }{
		{"2026-11-21T03:00:00+08:00", "2026-11-20"},
		{"2026-11-22T23:00:00+08:00", "2026-11-20"},
		{"2026-11-23T07:59:59+08:00", "2026-11-20"},
		{"2026-11-23T23:00:00+08:00", ""},
	} {
		at, _ := time.Parse(time.RFC3339, test.at)
		if got := preparationThrough(scope, at); got != test.want {
			t.Errorf("%s: got %s, want %s", test.at, got, test.want)
		}
	}
}
