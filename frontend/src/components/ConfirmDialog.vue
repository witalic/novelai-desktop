<script setup lang="ts">
import { useConfirm } from '../composables/useConfirm'

const { state, settle } = useConfirm()
</script>

<template>
  <Teleport to="body">
    <div v-if="state.open" class="confirm-back" @click="settle(false)" @contextmenu.prevent>
      <div class="confirm-card" @click.stop>
        <div class="ttl">{{ state.title }}</div>
        <div class="msg">{{ state.message }}</div>
        <label v-if="state.check" class="chk">
          <button type="button" class="box" :class="{ on: state.check.checked }" role="checkbox" :aria-checked="state.check.checked"
            @click="state.check.checked = !state.check.checked">{{ state.check.checked ? '✓' : '' }}</button>
          <span>{{ state.check.label }}<small v-if="state.check.hint">{{ state.check.hint }}</small></span>
        </label>
        <div class="acts">
          <button class="cancel" @click="settle(false)">{{ state.cancelLabel }}</button>
          <button class="ok" :class="{ danger: state.danger }" @click="settle(true)">{{ state.confirmLabel }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-back{position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:20px}
.confirm-card{width:min(400px,100%);background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg);
  box-shadow:0 18px 48px rgba(0,0,0,.5);padding:20px 22px}
.ttl{font-size:15px;font-weight:650;margin-bottom:8px}
.msg{font-size:13px;color:var(--text-dim);line-height:1.6}
.chk{display:flex;gap:9px;align-items:flex-start;margin-top:12px;font-size:13px;color:var(--text);cursor:pointer}
.chk small{display:block;color:var(--text-faint);font-size:11.5px;line-height:1.5;margin-top:1px}
.box{flex-shrink:0;width:16px;height:16px;margin-top:1px;padding:0;border-radius:4px;border:1.5px solid var(--border-strong);
  background:var(--surface-1);color:var(--on-accent);font-size:10px;font-weight:800;line-height:1;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center}
.box.on{background:var(--accent);border-color:var(--accent)}
.acts{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}
.acts button{border-radius:var(--radius);font-size:13px;font-weight:600;padding:8px 16px;cursor:pointer}
.acts .cancel{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim)}
.acts .cancel:hover{border-color:var(--border-strong);color:var(--text)}
.acts .ok{border:0;background:var(--accent);color:var(--on-accent)}
.acts .ok.danger{background:#c9372c}
.acts .ok:hover{opacity:.92}
</style>
