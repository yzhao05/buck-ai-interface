/* Hosted CSVs are staged in memory and archived on the server. */
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
  const legacy = {
    save(conversation, sha256, filename, blob, epoch = 0) {
      return transaction("readwrite", (store, done) => {
        const key=conversation+":"+sha256, request=store.get(key);
        request.onsuccess=()=>{
          // A slow background download must not overwrite a newer re-upload.
          if (!request.result || (request.result.epoch || 0)<=epoch) {
            const value={key,conversation,sha256,filename,blob,epoch,version:crypto.randomUUID()};
            store.put(value);done(value);
          }
        };
      });
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
          if (cursor) { if (keepEpoch === null || (cursor.value.epoch || 0) < keepEpoch) cursor.delete(); cursor.continue(); }
        };
      });
    }
  };
  // In Studio, IndexedDB is read solely to migrate old files. New blobs
  // are staged in RAM until the archive acknowledges their SHA-256.
  const staged = new Map();
  async function hasLegacyFiles() {
    if (!serverArchiveOnly()) return true;
    if (!window.indexedDB) return false;
    if (typeof indexedDB.databases!=="function") return true;
    return (await indexedDB.databases()).some(db=>db.name==="buck-csv-v1");
  }
  return {
    async save(conversation, sha256, filename, blob, epoch=0) {
      if (!serverArchiveOnly()) return legacy.save(conversation,sha256,filename,blob,epoch);
      const key=conversation+":"+sha256;
      if ((staged.get(key)?.epoch ?? -1)>epoch) return;
      const file={key,conversation,sha256,filename,blob,epoch,version:crypto.randomUUID()};
      staged.set(key,file);
      return file;
    },
    async load(conversation,sha256,epoch=0) {
      const item=staged.get(conversation+":"+sha256);
      return item?.epoch===epoch ? item : await hasLegacyFiles() ? legacy.load(conversation,sha256,epoch) : null;
    },
    async list(conversation) {
      const old=await hasLegacyFiles() ? await legacy.list(conversation) : [];
      if (!serverArchiveOnly()) return old;
      const files=new Map(old.map(f=>[f.key,f]));
      for (const file of staged.values()) if (file.conversation===conversation) files.set(file.key,file);
      return [...files.values()];
    },
    async acknowledged(file) {
      if (staged.get(file.key)===file) staged.delete(file.key);
      if (!await hasLegacyFiles()) return;
      await transaction("readwrite",store=>{
        const read=store.get(file.key);
        read.onsuccess=()=>{
          const old=read.result;
          if (old && (old.epoch || 0)===(file.epoch || 0) && old.sha256===file.sha256)
            store.delete(file.key);
        };
      });
    },
    async remove(conversation,keepEpoch=null) {
      if (await hasLegacyFiles()) await legacy.remove(conversation,keepEpoch);
      for(const [key,file] of staged)
        if(file.conversation===conversation && (keepEpoch===null || file.epoch<keepEpoch)) staged.delete(key);
    }
  };
})();
