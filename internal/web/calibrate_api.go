package web

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/westphae/kingfisher/internal/calibrate"
)

func (s *Server) handleCalibrate(w http.ResponseWriter, r *http.Request) {
	if s.cal == nil {
		http.Error(w, "calibrate unavailable", http.StatusServiceUnavailable)
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/api/calibrate")
	path = strings.Trim(path, "/")
	switch {
	case path == "session" && r.Method == http.MethodGet:
		s.cal.TickSeek()
		writeJSON(w, s.cal.State())
	case path == "session" && r.Method == http.MethodPost:
		s.handleCalibrateStart(w, r)
	case path == "lock" && r.Method == http.MethodPost:
		if err := s.cal.Lock(r.Context()); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, s.cal.State())
	case path == "retake" && r.Method == http.MethodPost:
		s.handleCalibrateRetake(w, r)
	case path == "fit" && r.Method == http.MethodPost:
		if err := s.cal.Fit(); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, s.cal.State())
	case path == "save" && r.Method == http.MethodPost:
		if err := s.cal.Save(); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, s.cal.State())
	case path == "cancel" && r.Method == http.MethodPost:
		s.cal.Cancel()
		writeJSON(w, s.cal.State())
	default:
		http.Error(w, "not found", http.StatusNotFound)
	}
}

func (s *Server) handleCalibrateStart(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Target string `json:"target"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	if err := s.cal.Start(calibrate.Target(body.Target)); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, s.cal.State())
}

func (s *Server) handleCalibrateRetake(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Face string `json:"face"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	if err := s.cal.Retake(calibrate.Face(body.Face)); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, s.cal.State())
}
