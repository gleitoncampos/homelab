package main

import (
        "fmt"
        "os"
        "regexp"

        bolt "go.etcd.io/bbolt"
)

var (
        reSwarmField = regexp.MustCompile(`(?is)("SwarmId"\s*:\s*")([^"]+)(")`)
)

func backupFile(src string) error {
        bak := src + ".bak"
        if err := os.Link(src, bak); err == nil {
                fmt.Printf("Backup criado (hardlink): %s\n", bak)
                return nil
        }
        data, err := os.ReadFile(src)
        if err != nil {
                return err
        }
        if err := os.WriteFile(bak, data, 0600); err != nil {
                return err
        }
        fmt.Printf("Backup criado (cópia): %s\n", bak)
        return nil
}

// --- patching helpers ---
func collectPatches(b *bolt.Bucket, bucketName, newID string, oldID *string) (keys [][]byte, vals [][]byte, count int) {
        c := b.Cursor()
        for k, v := c.First(); k != nil; k, v = c.Next() {
                if v == nil {
                        continue
                }
                if *oldID == "" {
                        if m := reSwarmField.FindSubmatch(v); len(m) == 4 {
                                *oldID = string(m[2])
                        }
                }
                nv := reSwarmField.ReplaceAllString(string(v), fmt.Sprintf("${1}%s${3}", newID))
                if nv != string(v) {
                        kCopy := append([]byte{}, k...)
                        vCopy := []byte(nv)
                        keys = append(keys, kCopy)
                        vals = append(vals, vCopy)
                        count++
                }
        }
        return
}

func applyPatches(b *bolt.Bucket, bucketName string, keys [][]byte, vals [][]byte) (int, error) {
        patched := 0
        for i := range keys {
                if err := b.Put(keys[i], vals[i]); err != nil {
                        return patched, err
                }
                patched++
                fmt.Printf("Patched %s key=%q\n", bucketName, string(keys[i]))
        }
        return patched, nil
}

func patchSingleBucket(db *bolt.DB, bucketName, newID string, oldID *string) (int, error) {
        fmt.Printf(">>> Abrindo transação para bucket: %s\n", bucketName)
        var klist [][]byte
        var vlist [][]byte

        if err := db.View(func(tx *bolt.Tx) error {
                b := tx.Bucket([]byte(bucketName))
                if b == nil {
                        fmt.Printf("Bucket %q não encontrado (ok)\n", bucketName)
                        return nil
                }
                klist, vlist, _ = collectPatches(b, bucketName, newID, oldID)
                return nil
        }); err != nil {
                return 0, err
        }

        if len(klist) == 0 {
                fmt.Printf(">>> Nada a aplicar em %s\n", bucketName)
                return 0, nil
        }

        var patched int
        if err := db.Update(func(tx *bolt.Tx) error {
                b := tx.Bucket([]byte(bucketName))
                if b == nil {
                        return nil
                }
                var err error
                patched, err = applyPatches(b, bucketName, klist, vlist)
                return err
        }); err != nil {
                return patched, err
        }

        fmt.Printf(">>> Transação concluída para %s (patched=%d)\n", bucketName, patched)
        return patched, nil
}

// --- compact helper ---
func compactDB(dbpath string) error {
        fmt.Println(">>> Iniciando compactação do DB...")
        tmp := dbpath + ".compact"
        src, err := bolt.Open(dbpath, 0600, &bolt.Options{ReadOnly: true})
        if err != nil {
                return err
        }
        defer src.Close()

        dst, err := bolt.Open(tmp, 0600, nil)
        if err != nil {
                return err
        }

        if err := bolt.Compact(dst, src, 0); err != nil {
                dst.Close()
                return err
        }
        if err := dst.Close(); err != nil {
                return err
        }

        if err := os.Remove(dbpath); err != nil {
                return err
        }
        if err := os.Rename(tmp, dbpath); err != nil {
                return err
        }
        fmt.Println(">>> Compactação concluída com sucesso")
        return nil
}

func main() {
        if len(os.Args) < 2 {
                fmt.Fprintf(os.Stderr, "uso:\n  %s /path/portainer.db NEW_ID   (patch)\n  %s /path/portainer.db --compact (compactar)\n", os.Args[0], os.Args[0])
                os.Exit(2)
        }

        dbpath := os.Args[1]

        // MODO COMPACTAÇÃO
        if len(os.Args) == 2 || (len(os.Args) == 3 && os.Args[2] == "--compact") {
                if err := compactDB(dbpath); err != nil {
                        fmt.Fprintf(os.Stderr, "compactação falhou: %v\n", err)
                        os.Exit(5)
                }
                return
        }

        // MODO PATCH
        newID := os.Args[2]
        if err := backupFile(dbpath); err != nil {
                fmt.Fprintf(os.Stderr, "aviso: backup falhou: %v\n", err)
        }

        db, err := bolt.Open(dbpath, 0600, nil)
        if err != nil {
                fmt.Fprintf(os.Stderr, "erro: não foi possível abrir %s: %v\n", dbpath, err)
                os.Exit(3)
        }
        defer db.Close()

        var oldID string
        totalPatched := 0

        for _, bucket := range []string{"stacks", "endpoints"} {
                patched, err := patchSingleBucket(db, bucket, newID, &oldID)
                if err != nil {
                        fmt.Fprintf(os.Stderr, "erro ao patchar %s: %v\n", bucket, err)
                        os.Exit(4)
                }
                totalPatched += patched
        }

        fmt.Println(">>> Patching concluído, preparando resumo...")
        switch {
        case oldID == "":
                fmt.Println("Nenhum SwarmId encontrado; nada a patchar (OK).")
        case oldID == newID:
                fmt.Printf("SwarmId já era o atual (%s); nada alterado (OK).\n", newID)
        default:
                fmt.Printf("OLD_ID=%s → NEW_ID=%s | registros alterados=%d\n", oldID, newID, totalPatched)
        }
        fmt.Println(">>> Programa finalizado com sucesso")
}
