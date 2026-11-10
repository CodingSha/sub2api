//go:build integration

package zhiyongli

import (
	"context"
	"testing"
	"time"
)

func TestAutomaticScheduleClosesWorkweekIsIdempotentAndPreparesDaily(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	tz := time.FixedZone("CST", 8*3600)
	if _, err := s.db.Exec("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'; UPDATE users SET role='admin' WHERE id=1; INSERT INTO audit_whitelist_users VALUES(3)"); err != nil {
		t.Fatal(err)
	}
	c := ScheduleConfig{Version: 1, Enabled: true, FirstWeek: "2026-09-07", ActorID: 1}
	before, err := s.ScheduleDue(ctx, c, time.Date(2026, 9, 12, 1, 59, 59, 0, tz))
	if err != nil || len(before.Jobs) != 0 {
		t.Fatal("must not start before freeze", err)
	}
	prepared, err := s.ScheduleDue(ctx, c, time.Date(2026, 9, 11, 2, 0, 0, 0, tz))
	if err != nil || prepared.Preparation == nil || prepared.Preparation.ThroughDate != "2026-09-10" || len(prepared.Preparation.Users) != 2 {
		t.Fatal("daily preparation and whitelist", err)
	}
	closed, err := s.ScheduleDue(ctx, c, time.Date(2026, 9, 12, 2, 0, 0, 0, tz))
	if err != nil || len(closed.Jobs) != 2 {
		t.Fatal("closed week must queue audited users", err)
	}
	again, err := s.ScheduleDue(ctx, c, time.Date(2026, 9, 13, 2, 0, 0, 0, tz))
	if err != nil || len(again.Jobs) != 2 || again.Jobs[0].ID != closed.Jobs[0].ID {
		t.Fatal("scheduler restart must reuse jobs", err)
	}
	if _, err = s.db.Exec("INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by) VALUES(2,'week','2026-09-07','2026-09-14','1.6',1)"); err != nil {
		t.Fatal("legacy scope must remain separate", err)
	}
	_, count, err := s.Jobs(ctx, 1, 20)
	if err != nil || count != 3 {
		t.Fatal("legacy task retained", err)
	}
	c.ActorID = 2
	if _, err = s.ScheduleDue(ctx, c, time.Date(2026, 9, 12, 2, 0, 0, 0, tz)); err == nil {
		t.Fatal("non-admin identity accepted")
	}
}

func TestStartLastWeekJobsCoversRosterExceptExcludedAndIsIdempotent(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	tz := time.FixedZone("CST", 8*3600)
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "22")
	if _, err := s.db.Exec("INSERT INTO users(id,email) SELECT n,'synthetic-'||n||'@example.test' FROM generate_series(4,101) n; INSERT INTO audit_whitelist_users VALUES(3)"); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 11, 9, 10, 0, 0, 0, tz)
	jobs, err := s.startLastWeekJobsAt(ctx, 1, now)
	if err != nil || len(jobs) != 99 {
		t.Fatal("101 accounts minus whitelist and excluded public account", len(jobs), err)
	}
	for _, j := range jobs {
		if j.UserID == 3 || j.UserID == 22 || j.PeriodType != "week" || j.PeriodStart != "2026-11-02" || j.WorkdayScope == nil {
			t.Fatalf("unexpected job %+v", j)
		}
	}
	again, err := s.startLastWeekJobsAt(ctx, 1, now.Add(time.Hour))
	if err != nil || len(again) != 99 || again[0].ID != jobs[0].ID || again[98].ID != jobs[98].ID {
		t.Fatal("repeat click must reuse jobs", err)
	}
	if _, count, err := s.Jobs(ctx, 1, 20); err != nil || count != 99 {
		t.Fatal("no duplicate jobs", count, err)
	}
}

func TestAutomaticScheduleHundredRosterAndHolidayNoScore(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	tz := time.FixedZone("CST", 8*3600)
	if _, err := s.db.Exec("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'; UPDATE users SET role='admin' WHERE id=1; INSERT INTO users(id,email) SELECT n,'synthetic-'||n||'@example.test' FROM generate_series(4,101) n; INSERT INTO audit_whitelist_users VALUES(3)"); err != nil {
		t.Fatal(err)
	}
	result, err := s.ScheduleDue(ctx, ScheduleConfig{Version: 1, Enabled: true, FirstWeek: "2026-09-07", ActorID: 1}, time.Date(2026, 9, 12, 2, 0, 0, 0, tz))
	if err != nil || len(result.Jobs) != 100 {
		t.Fatal("100 audited accounts should be queued in batches", len(result.Jobs), err)
	}
	holiday, err := s.ScheduleDue(ctx, ScheduleConfig{Version: 1, Enabled: true, FirstWeek: "2026-02-16", ActorID: 1}, time.Date(2026, 2, 23, 2, 0, 0, 0, tz))
	if err != nil || len(holiday.Jobs) != 0 || len(holiday.NonEvaluationWeeks) != 1 {
		t.Fatal("holiday cannot become employee inactivity", err)
	}
}
