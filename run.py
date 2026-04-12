from app import app, init_db
if __name__ == "__main__":
    init_db()
    print("\n  Cargo-Ledger  |  http://localhost:5000")
    print("  Manager: manager@cargo.com / manager123")
    print("  Govt:    govt@cargo.com    / govt1234")
    print("  Admin:   /admin/login      admin / admin@cargo2024\n")
    app.run(debug=False, host="0.0.0.0", port=5000)
