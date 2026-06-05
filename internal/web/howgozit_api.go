package web

import (
	"database/sql"
	"encoding/json"
	"log"
	"net/http"
	"strconv"
	"strings"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/howgozit"
	"github.com/westphae/kingfisher/internal/store"
)

func (s *Server) howgozitStore() *howgozit.Store {
	return howgozit.NewStore(s.store)
}

func (s *Server) handleHowgozit(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/howgozit")
	path = strings.Trim(path, "/")
	if path == "templates" || strings.HasPrefix(path, "templates/") {
		s.handleHowgozitTemplates(w, r, path)
		return
	}
	if path == "active_logs" {
		s.handleHowgozitActiveLogs(w, r)
		return
	}
	if path == "logs" || strings.HasPrefix(path, "logs/") {
		s.handleHowgozitLogs(w, r, path)
		return
	}
	http.NotFound(w, r)
}

func (s *Server) handleHowgozitTemplates(w http.ResponseWriter, r *http.Request, path string) {
	cfg := s.cfg.Get()
	switch {
	case path == "templates" && r.Method == http.MethodGet:
		writeJSON(w, map[string]any{
			"templates": cfg.Howgozit.Templates,
		})
	case path == "templates" && r.Method == http.MethodPost:
		var tmpl config.HowgozitTemplate
		if err := json.NewDecoder(r.Body).Decode(&tmpl); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := validateHowgozitTemplate(&tmpl, ""); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		cp := *cfg
		cp.Howgozit.Templates = append(cp.Howgozit.Templates, tmpl)
		if err := s.saveHowgozitConfig(&cp); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, tmpl)
	case strings.HasPrefix(path, "templates/"):
		id := strings.TrimPrefix(path, "templates/")
		if id == "" {
			http.NotFound(w, r)
			return
		}
		switch r.Method {
		case http.MethodPut:
			var tmpl config.HowgozitTemplate
			if err := json.NewDecoder(r.Body).Decode(&tmpl); err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			tmpl.ID = id
			if err := validateHowgozitTemplate(&tmpl, id); err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			cp := *cfg
			idx := -1
			for i := range cp.Howgozit.Templates {
				if cp.Howgozit.Templates[i].ID == id {
					idx = i
					break
				}
			}
			if idx < 0 {
				http.Error(w, "template not found", http.StatusNotFound)
				return
			}
			cp.Howgozit.Templates[idx] = tmpl
			if err := s.saveHowgozitConfig(&cp); err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			writeJSON(w, tmpl)
		case http.MethodDelete:
			cp := *cfg
			found := false
			out := cp.Howgozit.Templates[:0]
			for _, t := range cp.Howgozit.Templates {
				if t.ID == id {
					found = true
					continue
				}
				out = append(out, t)
			}
			if !found {
				http.Error(w, "template not found", http.StatusNotFound)
				return
			}
			cp.Howgozit.Templates = out
			active := cp.Howgozit.ActiveLogs[:0]
			for _, a := range cp.Howgozit.ActiveLogs {
				if a != id {
					active = append(active, a)
				}
			}
			cp.Howgozit.ActiveLogs = active
			if err := s.saveHowgozitConfig(&cp); err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			writeJSON(w, map[string]string{"status": "ok"})
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleHowgozitActiveLogs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPut {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		ActiveLogs []string `json:"active_logs"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	cfg := s.cfg.Get()
	cp := *cfg
	cp.Howgozit.ActiveLogs = append([]string(nil), body.ActiveLogs...)
	if err := s.saveHowgozitConfig(&cp); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, map[string]any{"active_logs": cp.Howgozit.ActiveLogs})
}

func (s *Server) handleHowgozitLogs(w http.ResponseWriter, r *http.Request, path string) {
	hs := s.howgozitStore()
	cfg := s.cfg.Get()

	if path == "logs" && r.Method == http.MethodGet {
		logs, err := hs.ListLogs()
		if err != nil {
			log.Printf("web: howgozit list logs: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]any{"logs": logs})
		return
	}
	if path == "logs" && r.Method == http.MethodPost {
		var body struct {
			TemplateID string `json:"template_id"`
			New        bool   `json:"new"`
			Name       string `json:"name"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		var meta *howgozit.LogMeta
		var err error
		switch {
		case body.New:
			name := strings.TrimSpace(body.Name)
			if name == "" {
				name = "New log"
			}
			meta, err = hs.CreateLog(name, nil, "", "")
		case body.TemplateID != "":
			tmpl := cfg.Howgozit.TemplateByID(body.TemplateID)
			if tmpl == nil {
				http.Error(w, "template not found", http.StatusNotFound)
				return
			}
			meta, err = hs.CreateLog(tmpl.Name, tmpl.Fields, tmpl.ID, "")
		default:
			http.Error(w, "template_id or new required", http.StatusBadRequest)
			return
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, meta)
		return
	}

	rest := strings.TrimPrefix(path, "logs/")
	parts := strings.Split(rest, "/")
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	logID := parts[0]

	if len(parts) == 1 && r.Method == http.MethodDelete {
		if err := hs.DeleteLog(logID); err != nil {
			if err == sql.ErrNoRows {
				http.Error(w, "log not found", http.StatusNotFound)
				return
			}
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]string{"status": "ok"})
		return
	}

	if len(parts) == 1 && r.Method == http.MethodPut {
		s.handleHowgozitUpdateLog(w, r, hs, logID)
		return
	}

	if len(parts) >= 2 && parts[1] == "rows" {
		s.handleHowgozitRows(w, r, hs, logID, parts[2:])
		return
	}
	if len(parts) == 2 && parts[1] == "schema" && r.Method == http.MethodPatch {
		s.handleHowgozitSchema(w, r, hs, logID)
		return
	}
	if len(parts) == 2 && parts[1] == "to-template" && r.Method == http.MethodPost {
		s.handleHowgozitToTemplate(w, r, hs, logID)
		return
	}
	http.NotFound(w, r)
}

func (s *Server) handleHowgozitUpdateLog(w http.ResponseWriter, r *http.Request, hs *howgozit.Store, logID string) {
	var body struct {
		DisplayName string                  `json:"display_name"`
		Fields      []config.HowgozitField `json:"fields"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	for i := range body.Fields {
		if err := validateHowgozitField(&body.Fields[i]); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
	}
	seen := map[string]bool{}
	for _, f := range body.Fields {
		if seen[f.Key] {
			http.Error(w, "duplicate field key", http.StatusBadRequest)
			return
		}
		seen[f.Key] = true
	}
	meta, err := hs.UpdateLogSchema(logID, body.DisplayName, body.Fields)
	if err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, "log not found", http.StatusNotFound)
			return
		}
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, meta)
}

func (s *Server) handleHowgozitSchema(w http.ResponseWriter, r *http.Request, hs *howgozit.Store, logID string) {
	var body struct {
		Field config.HowgozitField `json:"field"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := validateHowgozitField(&body.Field); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	meta, err := hs.AddField(logID, body.Field)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, meta)
}

func (s *Server) handleHowgozitToTemplate(w http.ResponseWriter, r *http.Request, hs *howgozit.Store, logID string) {
	meta, err := hs.GetLog(logID)
	if err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, "log not found", http.StatusNotFound)
			return
		}
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	var body struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		Replace bool   `json:"replace"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	id := store.Sanitize(body.ID)
	if id == "" {
		id = store.Sanitize(meta.DisplayName)
	}
	if id == "" {
		http.Error(w, "template id required", http.StatusBadRequest)
		return
	}
	name := strings.TrimSpace(body.Name)
	if name == "" {
		name = meta.DisplayName
	}
	tmpl := config.HowgozitTemplate{
		ID:     id,
		Name:   name,
		Fields: append([]config.HowgozitField(nil), meta.Fields...),
	}
	if err := validateHowgozitTemplate(&tmpl, ""); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	cfg := s.cfg.Get()
	cp := *cfg
	existing := -1
	for i := range cp.Howgozit.Templates {
		if cp.Howgozit.Templates[i].ID == id {
			existing = i
			break
		}
	}
	if existing >= 0 {
		if !body.Replace {
			http.Error(w, "template exists; set replace true to overwrite", http.StatusConflict)
			return
		}
		cp.Howgozit.Templates[existing] = tmpl
	} else {
		cp.Howgozit.Templates = append(cp.Howgozit.Templates, tmpl)
	}
	if err := s.saveHowgozitConfig(&cp); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, tmpl)
}

func (s *Server) handleHowgozitRows(w http.ResponseWriter, r *http.Request, hs *howgozit.Store, logID string, parts []string) {
	if len(parts) == 0 {
		switch r.Method {
		case http.MethodGet:
			rows, err := hs.ListRows(logID)
			if err != nil {
				if err == sql.ErrNoRows {
					http.Error(w, "log not found", http.StatusNotFound)
					return
				}
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			writeJSON(w, map[string]any{"rows": rows})
		case http.MethodPost:
			var body struct {
				TsNs   int64             `json:"ts_ns"`
				Values map[string]string `json:"values"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			row, err := hs.InsertRow(logID, body.TsNs, body.Values)
			if err != nil {
				if err == sql.ErrNoRows {
					http.Error(w, "log not found", http.StatusNotFound)
					return
				}
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			writeJSON(w, row)
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
		return
	}

	rowID, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil {
		http.Error(w, "invalid rowid", http.StatusBadRequest)
		return
	}

	switch r.Method {
	case http.MethodPatch:
		var body struct {
			TsNs   *int64            `json:"ts_ns"`
			Values map[string]string `json:"values"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := hs.UpdateRow(logID, rowID, body.TsNs, body.Values); err != nil {
			if err == sql.ErrNoRows {
				http.Error(w, "log not found", http.StatusNotFound)
				return
			}
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]string{"status": "ok"})
	case http.MethodDelete:
		if err := hs.DeleteRow(logID, rowID); err != nil {
			if err == sql.ErrNoRows {
				http.Error(w, "log not found", http.StatusNotFound)
				return
			}
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]string{"status": "ok"})
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) saveHowgozitConfig(cp *config.Config) error {
	s.cfg.Set(cp)
	return config.Save(s.cfg.Path(), cp)
}

func validateHowgozitTemplate(tmpl *config.HowgozitTemplate, existingID string) error {
	if tmpl == nil {
		return errBadTemplate("nil template")
	}
	if existingID == "" {
		if tmpl.ID == "" {
			return errBadTemplate("id required")
		}
	} else if tmpl.ID != existingID {
		return errBadTemplate("id mismatch")
	}
	id := store.Sanitize(tmpl.ID)
	if id == "" {
		return errBadTemplate("invalid id")
	}
	tmpl.ID = id
	if strings.TrimSpace(tmpl.Name) == "" {
		tmpl.Name = tmpl.ID
	}
	for i := range tmpl.Fields {
		if err := validateHowgozitField(&tmpl.Fields[i]); err != nil {
			return err
		}
	}
	return nil
}

func validateHowgozitField(f *config.HowgozitField) error {
	if f == nil {
		return errBadTemplate("nil field")
	}
	key := store.Sanitize(f.Key)
	if key == "" || key == "ts_ns" || key == "rowid" {
		return errBadTemplate("invalid field key")
	}
	f.Key = key
	if strings.TrimSpace(f.Label) == "" {
		f.Label = key
	}
	switch f.Type {
	case "number", "text", "select", "":
		if f.Type == "" {
			f.Type = "number"
		}
	default:
		return errBadTemplate("field type must be number, text, or select")
	}
	return nil
}

type templateError string

func (e templateError) Error() string { return string(e) }

func errBadTemplate(msg string) error { return templateError(msg) }

func stringInSlice(s string, list []string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
