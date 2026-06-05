package howgozit_test

import (
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/howgozit"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

func TestStoreCRUD(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(dir, "N99999")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	hs := howgozit.NewStore(st)
	tmpl := config.DefaultHowgozitTemplates()[0]
	meta, err := hs.EnsureLog(&tmpl)
	if err != nil {
		t.Fatal(err)
	}
	if meta.TableName != "hgz_atc_radio" {
		t.Fatalf("table: got %q want hgz_atc_radio", meta.TableName)
	}

	row, err := hs.InsertRow(meta.LogID, 1_700_000_000_000_000_000, map[string]string{
		"freq_mhz":  "124.2",
		"facility":  "SoCal Approach",
		"baro_inhg": "29.92",
	})
	if err != nil {
		t.Fatal(err)
	}
	if row.RowID <= 0 {
		t.Fatalf("rowid: %d", row.RowID)
	}

	if err := hs.UpdateRow(meta.LogID, row.RowID, nil, map[string]string{"freq_mhz": "124.45"}); err != nil {
		t.Fatal(err)
	}

	rows, err := hs.ListRows(meta.LogID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows: got %d want 1", len(rows))
	}
	if rows[0].Values["freq_mhz"] != "124.45" {
		t.Fatalf("freq: got %q", rows[0].Values["freq_mhz"])
	}
	if rows[0].Values["facility"] != "SoCal Approach" {
		t.Fatalf("facility: got %q", rows[0].Values["facility"])
	}

	if err := hs.DeleteRow(meta.LogID, row.RowID); err != nil {
		t.Fatal(err)
	}
	rows, err = hs.ListRows(meta.LogID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 0 {
		t.Fatalf("after delete: got %d rows", len(rows))
	}
}

func TestListLogsEmpty(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(filepath.Join(dir, "flights"), "N11111")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	hs := howgozit.NewStore(st)
	logs, err := hs.ListLogs()
	if err != nil {
		t.Fatal(err)
	}
	if len(logs) != 0 {
		t.Fatalf("logs: got %d want 0 on fresh flight DB", len(logs))
	}
}

func TestCreateLogFromTemplate(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(dir, "N99999")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	hs := howgozit.NewStore(st)
	tmpl := config.DefaultHowgozitTemplates()[0]
	m1, err := hs.CreateLog(tmpl.Name, tmpl.Fields, tmpl.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	m2, err := hs.CreateLog(tmpl.Name, tmpl.Fields, tmpl.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	if m1.LogID == m2.LogID {
		t.Fatalf("expected distinct log ids, both %q", m1.LogID)
	}
	if m2.LogID != "atc_radio_2" {
		t.Fatalf("log id: got %q want atc_radio_2", m2.LogID)
	}
}

func TestAddField(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(dir, "N99999")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	hs := howgozit.NewStore(st)
	meta, err := hs.CreateLog("Test", nil, "", "")
	if err != nil {
		t.Fatal(err)
	}
	updated, err := hs.AddField(meta.LogID, config.HowgozitField{
		Key: "remarks", Label: "Remarks", Type: "text",
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(updated.Fields) != 1 {
		t.Fatalf("fields: got %d want 1", len(updated.Fields))
	}
}

func TestAddFieldConcurrentSensorFlush(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(dir, "N99999")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	buf := store.NewBuffer(st, 10*time.Millisecond)
	stop := make(chan struct{})
	go buf.Run(stop)
	defer func() {
		close(stop)
		time.Sleep(30 * time.Millisecond)
	}()

	hs := howgozit.NewStore(st)
	meta, err := hs.CreateLog("Test", nil, "", "")
	if err != nil {
		t.Fatal(err)
	}

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		ts := time.Now().UnixNano()
		for i := 0; i < 200; i++ {
			buf.Append(live.Sample{
				Device: "gps",
				TsNs:   ts + int64(i),
				Values: map[string]float64{"speed_m_s": float64(i)},
			})
		}
	}()
	wg.Wait()

	updated, err := hs.AddField(meta.LogID, config.HowgozitField{
		Key: "fuel_pressure", Label: "Fuel Pressure", Type: "number", Unit: "psi",
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(updated.Fields) != 1 || updated.Fields[0].Key != "fuel_pressure" {
		t.Fatalf("fields: %+v", updated.Fields)
	}
}

func TestGetLogNormalizesLegacyDecimalType(t *testing.T) {
	st, hs := openTestStore(t)
	meta, err := hs.CreateLog("Test", nil, "", "legacy_log")
	if err != nil {
		t.Fatal(err)
	}
	_, err = st.DB().Exec(`UPDATE howgozit_log SET schema_json=? WHERE log_id=?`,
		`{"name":"Test","fields":[{"key":"mp_inhg","label":"MP","type":"decimal","step":"0.01","unit":"inHg"}]}`,
		meta.LogID)
	if err != nil {
		t.Fatal(err)
	}
	got, err := hs.GetLog(meta.LogID)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Fields) != 1 {
		t.Fatalf("fields: got %d want 1", len(got.Fields))
	}
	if got.Fields[0].Type != "number" {
		t.Fatalf("type: got %q want number", got.Fields[0].Type)
	}
	if got.Fields[0].InputMode != "decimal" {
		t.Fatalf("input_mode: got %q want decimal", got.Fields[0].InputMode)
	}
}

func TestInsertRowUppercaseText(t *testing.T) {
	_, hs := openTestStore(t)
	meta, err := hs.CreateLog("ATIS", []config.HowgozitField{
		{Key: "airport", Label: "Airport", Type: "text", Uppercase: true},
	}, "", "atis")
	if err != nil {
		t.Fatal(err)
	}
	row, err := hs.InsertRow(meta.LogID, time.Now().UnixNano(), map[string]string{"airport": "klax"})
	if err != nil {
		t.Fatal(err)
	}
	if row.Values["airport"] != "KLAX" {
		t.Fatalf("airport: got %q want KLAX", row.Values["airport"])
	}
}

func openTestStore(t *testing.T) (*store.Store, *howgozit.Store) {
	t.Helper()
	dir := t.TempDir()
	st, err := store.Open(dir, "N99999")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	return st, howgozit.NewStore(st)
}

func tableColumnNames(t *testing.T, st *store.Store, table string) []string {
	t.Helper()
	rows, err := st.DB().Query(`PRAGMA table_info("` + table + `")`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var names []string
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt interface{}
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			t.Fatal(err)
		}
		if name != "ts_ns" {
			names = append(names, name)
		}
	}
	return names
}

func TestUpdateLogSchemaRename(t *testing.T) {
	_, hs := openTestStore(t)
	tmpl := config.DefaultHowgozitTemplates()[0]
	meta, err := hs.CreateLog(tmpl.Name, tmpl.Fields, tmpl.ID, "atc_test")
	if err != nil {
		t.Fatal(err)
	}
	updated, err := hs.UpdateLogSchema(meta.LogID, "ATC Comm", meta.Fields)
	if err != nil {
		t.Fatal(err)
	}
	if updated.DisplayName != "ATC Comm" {
		t.Fatalf("name: got %q", updated.DisplayName)
	}
}

func TestUpdateLogSchemaReorder(t *testing.T) {
	_, hs := openTestStore(t)
	tmpl := config.DefaultHowgozitTemplates()[0]
	meta, err := hs.CreateLog(tmpl.Name, tmpl.Fields, tmpl.ID, "atc_test")
	if err != nil {
		t.Fatal(err)
	}
	fields := []config.HowgozitField{
		tmpl.Fields[2], tmpl.Fields[0], tmpl.Fields[1],
	}
	updated, err := hs.UpdateLogSchema(meta.LogID, meta.DisplayName, fields)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Fields[0].Key != "baro_inhg" {
		t.Fatalf("order: first key %q want baro_inhg", updated.Fields[0].Key)
	}
}

func TestUpdateLogSchemaAddRemoveColumn(t *testing.T) {
	st, hs := openTestStore(t)
	meta, err := hs.CreateLog("Test", []config.HowgozitField{
		{Key: "a", Label: "A", Type: "number"},
		{Key: "b", Label: "B", Type: "text"},
	}, "", "test_log")
	if err != nil {
		t.Fatal(err)
	}
	fields := []config.HowgozitField{
		{Key: "a", Label: "A", Type: "number"},
		{Key: "c", Label: "C", Type: "text"},
	}
	updated, err := hs.UpdateLogSchema(meta.LogID, meta.DisplayName, fields)
	if err != nil {
		t.Fatal(err)
	}
	if len(updated.Fields) != 2 {
		t.Fatalf("fields: got %d want 2", len(updated.Fields))
	}
	cols := tableColumnNames(t, st, meta.TableName)
	if len(cols) != 2 || cols[0] != "a" || cols[1] != "c" {
		t.Fatalf("columns: %v", cols)
	}
}

func TestUpdateLogSchemaRejectDuplicateKeys(t *testing.T) {
	_, hs := openTestStore(t)
	meta, err := hs.CreateLog("Test", nil, "", "")
	if err != nil {
		t.Fatal(err)
	}
	_, err = hs.UpdateLogSchema(meta.LogID, meta.DisplayName, []config.HowgozitField{
		{Key: "x", Label: "X", Type: "number"},
		{Key: "x", Label: "Y", Type: "number"},
	})
	if err == nil {
		t.Fatal("expected duplicate key error")
	}
}

func TestUpdateLogSchemaRejectTypeChange(t *testing.T) {
	_, hs := openTestStore(t)
	meta, err := hs.CreateLog("Test", []config.HowgozitField{
		{Key: "n", Label: "N", Type: "number"},
	}, "", "")
	if err != nil {
		t.Fatal(err)
	}
	_, err = hs.UpdateLogSchema(meta.LogID, meta.DisplayName, []config.HowgozitField{
		{Key: "n", Label: "N", Type: "text"},
	})
	if err == nil {
		t.Fatal("expected type change error")
	}
}
