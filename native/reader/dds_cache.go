package main

import (
	"crypto/sha256"
	"encoding/json"
	"os"
	"path/filepath"
)

type ddsCache struct {
	Reader string         `json:"reader_sha256"`
	Files  map[string]int `json:"verified_dds"`
}

func loadDDSCache() (map[string]int, string, string) {
	files := map[string]int{}
	if len(os.Args) < 3 {
		return files, "", ""
	}
	exe, e := os.Executable()
	must(e)
	raw, e := os.ReadFile(exe)
	must(e)
	digest := sha256.Sum256(raw)
	hash := fmtHash(digest)
	path := filepath.Join(os.Args[2], "verified_dds.json")
	raw, e = os.ReadFile(path)
	if e == nil {
		var c ddsCache
		if json.Unmarshal(raw, &c) == nil && c.Reader == hash && c.Files != nil {
			files = c.Files
		}
	}
	return files, path, hash
}
func fmtHash(v [32]byte) string {
	const h = "0123456789abcdef"
	b := make([]byte, 64)
	for i, x := range v {
		b[i*2] = h[x>>4]
		b[i*2+1] = h[x&15]
	}
	return string(b)
}
func saveDDSCache(path, reader string, files map[string]int) {
	if path == "" {
		return
	}
	must(os.MkdirAll(filepath.Dir(path), 0755))
	raw, e := json.Marshal(ddsCache{reader, files})
	must(e)
	pending := path + ".new"
	must(os.WriteFile(pending, raw, 0644))
	must(os.Rename(pending, path))
}
