// Package howgozit persists in-flight manual log rows in the current flight DB.
// Each log instance gets an hgz_* data table with ts_ns plus typed columns.
package howgozit

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/store"
)

const tablePrefix = "hgz_"

// LogMeta describes one manual log instance in the flight DB.
type LogMeta struct {
	LogID       string                 `json:"log_id"`
	TemplateID  string                 `json:"template_id"`
	DisplayName string                 `json:"display_name"`
	TableName   string                 `json:"table_name"`
	SchemaJSON  string                 `json:"schema_json"`
	Fields      []config.HowgozitField `json:"fields"`
	CreatedTsNs int64                  `json:"created_ts_ns"`
}

// Row is one manual log entry.
type Row struct {
	RowID  int64             `json:"rowid"`
	TsNs   int64             `json:"ts_ns"`
	Values map[string]string `json:"values"`
}

// Store wraps flight DB access for howgozit tables.
type Store struct {
	db *sql.DB
}

// NewStore attaches to an open flight database.
func NewStore(st *store.Store) *Store {
	return &Store{db: st.DB()}
}

func dataTableName(logID string) string {
	return tablePrefix + store.Sanitize(logID)
}

func columnSQLType(fieldType string) string {
	switch fieldType {
	case "text", "select":
		return "TEXT"
	default:
		return "REAL"
	}
}

func (s *Store) ensureDataTable(table string, fields []config.HowgozitField) error {
	return ensureDataTable(s.db, table, fields)
}

func ensureDataTable(conn dbConn, table string, fields []config.HowgozitField) error {
	colDecl := make([]string, 0, len(fields))
	wantCols := make([]string, 0, len(fields))
	for _, f := range fields {
		key := store.Sanitize(f.Key)
		if key == "" || key == "ts_ns" || key == "rowid" {
			continue
		}
		wantCols = append(wantCols, key)
		colDecl = append(colDecl, fmt.Sprintf(`%q %s`, key, columnSQLType(f.Type)))
	}
	stmt := fmt.Sprintf(`CREATE TABLE IF NOT EXISTS %q (ts_ns INTEGER NOT NULL`, table)
	if len(colDecl) > 0 {
		stmt += ", " + strings.Join(colDecl, ", ")
	}
	stmt += ")"
	if _, err := conn.Exec(stmt); err != nil {
		return err
	}
	return addMissingColumns(conn, table, fields, wantCols)
}

type dbConn interface {
	Exec(query string, args ...any) (sql.Result, error)
	Query(query string, args ...any) (*sql.Rows, error)
}

func addMissingColumns(conn dbConn, table string, fields []config.HowgozitField, wantCols []string) error {
	rows, err := conn.Query(fmt.Sprintf(`PRAGMA table_info(%q)`, table))
	if err != nil {
		return err
	}
	defer rows.Close()
	have := map[string]bool{}
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return err
		}
		have[name] = true
	}
	if err := rows.Err(); err != nil {
		return err
	}
	fieldType := map[string]string{}
	for _, f := range fields {
		key := store.Sanitize(f.Key)
		if key != "" {
			fieldType[key] = f.Type
		}
	}
	for _, c := range wantCols {
		if have[c] {
			continue
		}
		ft := fieldType[c]
		if ft == "" {
			ft = "number"
		}
		if _, err := conn.Exec(fmt.Sprintf(`ALTER TABLE %q ADD COLUMN %q %s`, table, c, columnSQLType(ft))); err != nil {
			return err
		}
	}
	return nil
}

// EnsureLog creates a log from a template using the template id as log_id when free.
// Deprecated for new code: use CreateLog so each instance gets a unique id.
func (s *Store) EnsureLog(tmpl *config.HowgozitTemplate) (*LogMeta, error) {
	if tmpl == nil || tmpl.ID == "" {
		return nil, fmt.Errorf("howgozit: empty template")
	}
	if meta, err := s.GetLog(store.Sanitize(tmpl.ID)); err == nil && meta != nil {
		if err := s.ensureDataTable(meta.TableName, meta.Fields); err != nil {
			return nil, err
		}
		return meta, nil
	} else if err != nil && err != sql.ErrNoRows {
		return nil, err
	}
	return s.CreateLog(tmpl.Name, tmpl.Fields, tmpl.ID, store.Sanitize(tmpl.ID))
}

// CreateLog opens a new log instance with a unique log_id. seedTemplateID is optional
// (records which template was used as a seed) and preferredLogID may pin the id when unused.
func (s *Store) CreateLog(displayName string, fields []config.HowgozitField, seedTemplateID, preferredLogID string) (*LogMeta, error) {
	displayName = strings.TrimSpace(displayName)
	if displayName == "" {
		displayName = "Log"
	}
	fields = append([]config.HowgozitField(nil), fields...)
	logID := store.Sanitize(preferredLogID)
	if logID == "" {
		base := store.Sanitize(displayName)
		if base == "" {
			base = "log"
		}
		var err error
		logID, err = s.uniqueLogID(base)
		if err != nil {
			return nil, err
		}
	} else if _, err := s.GetLog(logID); err == nil {
		return nil, fmt.Errorf("howgozit: log %q already exists", logID)
	} else if err != sql.ErrNoRows {
		return nil, err
	}
	table := dataTableName(logID)
	if err := s.ensureDataTable(table, fields); err != nil {
		return nil, err
	}
	snap := config.HowgozitTemplate{Name: displayName, Fields: fields}
	schemaJSON, err := json.Marshal(snap)
	if err != nil {
		return nil, err
	}
	seed := store.Sanitize(seedTemplateID)
	now := time.Now().UnixNano()
	_, err = s.db.Exec(`INSERT INTO howgozit_log(log_id,template_id,display_name,schema_json,table_name,created_ts_ns)
  VALUES(?,?,?,?,?,?)`,
		logID, seed, displayName, string(schemaJSON), table, now)
	if err != nil {
		return nil, err
	}
	return &LogMeta{
		LogID:       logID,
		TemplateID:  seed,
		DisplayName: displayName,
		TableName:   table,
		SchemaJSON:  string(schemaJSON),
		Fields:      fields,
		CreatedTsNs: now,
	}, nil
}

func (s *Store) uniqueLogID(base string) (string, error) {
	for i := 0; i < 1000; i++ {
		id := base
		if i > 0 {
			id = fmt.Sprintf("%s_%d", base, i+1)
		}
		id = store.Sanitize(id)
		if id == "" {
			continue
		}
		_, err := s.GetLog(id)
		if err == sql.ErrNoRows {
			return id, nil
		}
		if err != nil {
			return "", err
		}
	}
	return store.Sanitize(fmt.Sprintf("log_%d", time.Now().UnixNano())), nil
}

// AddField appends a column to the log schema and SQLite table.
func (s *Store) AddField(logID string, field config.HowgozitField) (*LogMeta, error) {
	meta, err := s.GetLog(logID)
	if err != nil {
		return nil, err
	}
	key := store.Sanitize(field.Key)
	if key == "" || key == "ts_ns" || key == "rowid" {
		return nil, fmt.Errorf("howgozit: invalid field key")
	}
	field.Key = key
	if strings.TrimSpace(field.Label) == "" {
		field.Label = key
	}
	field.Type = strings.ToLower(strings.TrimSpace(field.Type))
	config.NormalizeHowgozitField(&field)
	switch field.Type {
	case "number", "text", "select":
	default:
		return nil, fmt.Errorf("howgozit: field type must be number, text, or select")
	}
	for _, f := range meta.Fields {
		if store.Sanitize(f.Key) == key {
			return nil, fmt.Errorf("howgozit: duplicate field %q", key)
		}
	}
	meta.Fields = append(meta.Fields, field)
	return s.persistSchema(meta)
}

// UpdateLogSchema replaces display name and field schema; adds/drops SQLite columns as needed.
func (s *Store) UpdateLogSchema(logID, displayName string, fields []config.HowgozitField) (*LogMeta, error) {
	meta, err := s.GetLog(logID)
	if err != nil {
		return nil, err
	}
	displayName = strings.TrimSpace(displayName)
	if displayName == "" {
		displayName = meta.DisplayName
	}

	normalized, err := normalizeLogFields(fields)
	if err != nil {
		return nil, err
	}
	oldByKey := make(map[string]config.HowgozitField, len(meta.Fields))
	for _, f := range meta.Fields {
		key := store.Sanitize(f.Key)
		if key != "" {
			oldByKey[key] = f
		}
	}
	newByKey := make(map[string]config.HowgozitField, len(normalized))
	for _, f := range normalized {
		newByKey[store.Sanitize(f.Key)] = f
	}
	for key, old := range oldByKey {
		newF, ok := newByKey[key]
		if !ok {
			continue
		}
		if columnSQLType(old.Type) != columnSQLType(newF.Type) {
			return nil, fmt.Errorf("howgozit: cannot change type of field %q", key)
		}
	}

	tx, err := s.db.Begin()
	if err != nil {
		return nil, err
	}
	rollback := func(e error) (*LogMeta, error) {
		tx.Rollback()
		return nil, e
	}

	for key := range oldByKey {
		if _, ok := newByKey[key]; ok {
			continue
		}
		if _, err := tx.Exec(fmt.Sprintf(`ALTER TABLE %q DROP COLUMN %q`, meta.TableName, key)); err != nil {
			return rollback(err)
		}
	}
	for key, f := range newByKey {
		if _, ok := oldByKey[key]; ok {
			continue
		}
		if _, err := tx.Exec(fmt.Sprintf(`ALTER TABLE %q ADD COLUMN %q %s`, meta.TableName, key, columnSQLType(f.Type))); err != nil {
			return rollback(err)
		}
	}

	snap := config.HowgozitTemplate{Name: displayName, Fields: normalized}
	schemaJSON, err := json.Marshal(snap)
	if err != nil {
		return rollback(err)
	}
	if _, err := tx.Exec(`UPDATE howgozit_log SET display_name=?, schema_json=? WHERE log_id=?`,
		displayName, string(schemaJSON), meta.LogID); err != nil {
		return rollback(err)
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}

	meta.DisplayName = displayName
	meta.Fields = normalized
	meta.SchemaJSON = string(schemaJSON)
	return meta, nil
}

func normalizeLogFields(fields []config.HowgozitField) ([]config.HowgozitField, error) {
	out := make([]config.HowgozitField, 0, len(fields))
	seen := map[string]bool{}
	for _, f := range fields {
		key := store.Sanitize(f.Key)
		if key == "" || key == "ts_ns" || key == "rowid" {
			return nil, fmt.Errorf("howgozit: invalid field key")
		}
		if seen[key] {
			return nil, fmt.Errorf("howgozit: duplicate field %q", key)
		}
		seen[key] = true
		f.Key = key
		if strings.TrimSpace(f.Label) == "" {
			f.Label = key
		}
		f.Type = strings.ToLower(strings.TrimSpace(f.Type))
		config.NormalizeHowgozitField(&f)
		switch f.Type {
		case "number", "text", "select":
		default:
			return nil, fmt.Errorf("howgozit: field type must be number, text, or select")
		}
		out = append(out, f)
	}
	return out, nil
}

func normalizeLogMetaFields(fields []config.HowgozitField) {
	for i := range fields {
		config.NormalizeHowgozitField(&fields[i])
	}
}

func (s *Store) persistSchema(meta *LogMeta) (*LogMeta, error) {
	normalizeLogMetaFields(meta.Fields)
	snap := config.HowgozitTemplate{Name: meta.DisplayName, Fields: meta.Fields}
	schemaJSON, err := json.Marshal(snap)
	if err != nil {
		return nil, err
	}
	tx, err := s.db.Begin()
	if err != nil {
		return nil, err
	}
	rollback := func(e error) (*LogMeta, error) {
		tx.Rollback()
		return nil, e
	}
	if err := ensureDataTable(tx, meta.TableName, meta.Fields); err != nil {
		return rollback(err)
	}
	if _, err := tx.Exec(`UPDATE howgozit_log SET display_name=?, schema_json=? WHERE log_id=?`,
		meta.DisplayName, string(schemaJSON), meta.LogID); err != nil {
		return rollback(err)
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	meta.SchemaJSON = string(schemaJSON)
	return meta, nil
}

// ListLogs returns all log instances in the current flight DB.
func (s *Store) ListLogs() ([]LogMeta, error) {
	rows, err := s.db.Query(`SELECT log_id,template_id,display_name,schema_json,table_name,created_ts_ns
  FROM howgozit_log ORDER BY display_name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []LogMeta
	for rows.Next() {
		var m LogMeta
		if err := rows.Scan(&m.LogID, &m.TemplateID, &m.DisplayName, &m.SchemaJSON, &m.TableName, &m.CreatedTsNs); err != nil {
			return nil, err
		}
		var tmpl config.HowgozitTemplate
		if err := json.Unmarshal([]byte(m.SchemaJSON), &tmpl); err == nil {
			m.Fields = tmpl.Fields
			normalizeLogMetaFields(m.Fields)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// GetLog returns one log instance by id.
func (s *Store) GetLog(logID string) (*LogMeta, error) {
	id := store.Sanitize(logID)
	var m LogMeta
	err := s.db.QueryRow(`SELECT log_id,template_id,display_name,schema_json,table_name,created_ts_ns
  FROM howgozit_log WHERE log_id=?`, id).
		Scan(&m.LogID, &m.TemplateID, &m.DisplayName, &m.SchemaJSON, &m.TableName, &m.CreatedTsNs)
	if err != nil {
		return nil, err
	}
	var tmpl config.HowgozitTemplate
	if err := json.Unmarshal([]byte(m.SchemaJSON), &tmpl); err == nil {
		m.Fields = tmpl.Fields
		normalizeLogMetaFields(m.Fields)
	}
	return &m, nil
}

// DeleteLog drops the data table and registry row.
func (s *Store) DeleteLog(logID string) error {
	meta, err := s.GetLog(logID)
	if err != nil {
		return err
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	if _, err := tx.Exec(fmt.Sprintf(`DROP TABLE IF EXISTS %q`, meta.TableName)); err != nil {
		tx.Rollback()
		return err
	}
	if _, err := tx.Exec(`DELETE FROM howgozit_log WHERE log_id=?`, meta.LogID); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit()
}

// ListRows returns all rows for a log ordered by ts_ns.
func (s *Store) ListRows(logID string) ([]Row, error) {
	meta, err := s.GetLog(logID)
	if err != nil {
		return nil, err
	}
	colNames := make([]string, 0, len(meta.Fields))
	for _, f := range meta.Fields {
		key := store.Sanitize(f.Key)
		if key != "" && key != "ts_ns" {
			colNames = append(colNames, key)
		}
	}
	selectCols := `rowid, ts_ns`
	for _, c := range colNames {
		selectCols += fmt.Sprintf(`, %q`, c)
	}
	q := fmt.Sprintf(`SELECT %s FROM %q ORDER BY ts_ns`, selectCols, meta.TableName)
	rows, err := s.db.Query(q)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Row
	for rows.Next() {
		dest := make([]any, 2+len(colNames))
		var rowID int64
		var tsNs int64
		dest[0] = &rowID
		dest[1] = &tsNs
		colPtrs := make([]sql.NullString, len(colNames))
		for i := range colNames {
			dest[2+i] = &colPtrs[i]
		}
		if err := rows.Scan(dest...); err != nil {
			return nil, err
		}
		vals := make(map[string]string, len(colNames))
		for i, c := range colNames {
			if colPtrs[i].Valid {
				vals[c] = colPtrs[i].String
			}
		}
		out = append(out, Row{RowID: rowID, TsNs: tsNs, Values: vals})
	}
	return out, rows.Err()
}

// InsertRow adds a row; ts_ns defaults to now when zero.
func (s *Store) InsertRow(logID string, tsNs int64, values map[string]string) (*Row, error) {
	meta, err := s.GetLog(logID)
	if err != nil {
		return nil, err
	}
	if err := s.ensureDataTable(meta.TableName, meta.Fields); err != nil {
		return nil, err
	}
	if tsNs <= 0 {
		tsNs = time.Now().UnixNano()
	}
	colNames := make([]string, 0, len(meta.Fields))
	for _, f := range meta.Fields {
		key := store.Sanitize(f.Key)
		if key != "" && key != "ts_ns" {
			colNames = append(colNames, key)
		}
	}
	fieldMeta := fieldByKey(meta.Fields)
	colList := []string{`"ts_ns"`}
	placeholders := []string{"?"}
	args := []any{tsNs}
	for _, c := range colNames {
		colList = append(colList, fmt.Sprintf("%q", c))
		placeholders = append(placeholders, "?")
		args = append(args, sqlValue(fieldMeta[c], values[c]))
	}
	stmt := fmt.Sprintf(`INSERT INTO %q (%s) VALUES (%s)`,
		meta.TableName, strings.Join(colList, ","), strings.Join(placeholders, ","))
	res, err := s.db.Exec(stmt, args...)
	if err != nil {
		return nil, err
	}
	rid, err := res.LastInsertId()
	if err != nil {
		return nil, err
	}
	outVals := make(map[string]string, len(colNames))
	for _, c := range colNames {
		raw := values[c]
		if raw == "" {
			if v, ok := values[unsanitizeKey(meta.Fields, c)]; ok {
				raw = v
			}
		}
		_, outVals[c] = formatCellString(fieldMeta[c], raw)
	}
	return &Row{RowID: rid, TsNs: tsNs, Values: outVals}, nil
}

func unsanitizeKey(fields []config.HowgozitField, sanitized string) string {
	for _, f := range fields {
		if store.Sanitize(f.Key) == sanitized {
			return f.Key
		}
	}
	return sanitized
}

func fieldByKey(fields []config.HowgozitField) map[string]config.HowgozitField {
	out := make(map[string]config.HowgozitField, len(fields))
	for _, f := range fields {
		key := store.Sanitize(f.Key)
		if key != "" {
			out[key] = f
		}
	}
	return out
}

// formatCellString normalizes a manual-log cell for storage/display.
func formatCellString(f config.HowgozitField, s string) (sql any, stored string) {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil, ""
	}
	switch f.Type {
	case "text":
		if f.Uppercase {
			s = strings.ToUpper(s)
		}
		return s, s
	case "select":
		return s, s
	default:
		v, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return nil, ""
		}
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, ""
		}
		return v, s
	}
}

func sqlValue(f config.HowgozitField, s string) any {
	v, _ := formatCellString(f, s)
	return v
}

// UpdateRow patches ts_ns and/or field values.
func (s *Store) UpdateRow(logID string, rowID int64, tsNs *int64, values map[string]string) error {
	meta, err := s.GetLog(logID)
	if err != nil {
		return err
	}
	sets := make([]string, 0, len(values)+1)
	args := make([]any, 0, len(values)+2)
	if tsNs != nil {
		sets = append(sets, "ts_ns=?")
		args = append(args, *tsNs)
	}
	fieldMeta := fieldByKey(meta.Fields)
	for k, v := range values {
		col := store.Sanitize(k)
		if col == "" || col == "ts_ns" || col == "rowid" {
			continue
		}
		sets = append(sets, fmt.Sprintf("%q=?", col))
		args = append(args, sqlValue(fieldMeta[col], v))
	}
	if len(sets) == 0 {
		return fmt.Errorf("howgozit: nothing to update")
	}
	args = append(args, rowID)
	stmt := fmt.Sprintf(`UPDATE %q SET %s WHERE rowid=?`, meta.TableName, strings.Join(sets, ","))
	_, err = s.db.Exec(stmt, args...)
	return err
}

// DeleteRow removes one row.
func (s *Store) DeleteRow(logID string, rowID int64) error {
	meta, err := s.GetLog(logID)
	if err != nil {
		return err
	}
	_, err = s.db.Exec(fmt.Sprintf(`DELETE FROM %q WHERE rowid=?`, meta.TableName), rowID)
	return err
}
