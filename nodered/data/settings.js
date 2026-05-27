/**
 * settings.js — Configurazione Node-RED per il CoAP Stack
 * Progetto didattico ITS 3° anno
 *
 * Nota: credentialSecret: false disabilita la cifratura delle credenziali
 * → utile in sviluppo per leggere facilmente flows_cred.json
 * → NON usare così in produzione!
 */
module.exports = {
    // Porta HTTP del pannello Node-RED
    uiPort: 1880,

    // Disabilita cifratura credenziali (solo sviluppo)
    credentialSecret: false,

    // Diagnostica
    logging: {
        console: {
            level: "info",
            metric: false,
            audit: false,
        },
    },

    // Editor abilitato (per modificare i flussi via browser)
    editorTheme: {
        page: {
            title: "CoAP IoT Stack — ITS",
        },
        header: {
            title: "CoAP IoT Stack",
        },
    },

    // Directory dei flussi (montata come volume da ./data/)
    userDir: "/data",

    // Timeout di completamento nodo (ms)
    functionTimeout: 10000,

    // Abilita i nodi globali di contesto
    contextStorage: {
        default: {
            module: "memory",
        },
    },
};
