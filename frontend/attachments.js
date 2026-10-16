/* Original CSV blobs live in IndexedDB, isolated by project/backend/conversation.
   Keep large files out of localStorage; never store credentials here. */
"use strict";
window.BuckAttachments = (() => {
  function database() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open("buck-csv-v1", 1);
      let blocked = false;
      request.onupgradeneeded = () => {
        const store = request.result.createObjectStore("files", {keyPath:"key"});
        store.createIndex("conversation", "conversation");
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () => { blocked = true; reject(new Error("CSV storage is blocked")); };
      request.onsuccess = () => {
        if (blocked) { request.result.close(); return; }
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
    });
  }
  async function transaction(mode, operation) {
    const db = await database();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction("files", mode);
        let result;
        tx.oncomplete = () => resolve(result);
        tx.onabort = tx.onerror = () => reject(tx.error || new Error("CSV storage failed"));
        operation(tx.objectStore("files"), value => { result = value; });
      });
    } finally { db.close(); }
  }
  return {
    save(conversation, sha256, filename, blob, epoch = 0) {
      return transaction("readwrite", store => store.put({key:conversation+":"+sha256, conversation, sha256, filename, blob, epoch}));
    },
    load(conversation, sha256, epoch = 0) {
      return transaction("readonly", (store, done) => {
        const request = store.get(conversation+":"+sha256);
        request.onsuccess = () => done(request.result && (request.result.epoch || 0) === epoch ? request.result : null);
      });
    },
    list(conversation) {
      return transaction("readonly", (store, done) => {
        const request = store.index("conversation").getAll(IDBKeyRange.only(conversation));
        request.onsuccess = () => done(request.result);
      });
    },
    remove(conversation, keepEpoch = null) {
      return transaction("readwrite", store => {
        const request = store.index("conversation").openCursor(IDBKeyRange.only(conversation));
        request.onsuccess = () => {
          const cursor = request.result;
          if (cursor) { if (keepEpoch === null || (cursor.value.epoch || 0) !== keepEpoch) cursor.delete(); cursor.continue(); }
        };
      });
    }
  };
})();
