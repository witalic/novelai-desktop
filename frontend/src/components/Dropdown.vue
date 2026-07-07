<script setup lang="ts">
import { computed, ref } from 'vue'

interface Opt { value: string | number; label: string; group?: string }
const props = defineProps<{ modelValue: string | number; options: Opt[] }>()
const emit = defineEmits<{ 'update:modelValue': [string | number] }>()

const open = ref(false)
const pos = ref({ left: 0, top: 0, width: 0 })
const btn = ref<HTMLElement | null>(null)

const selectedLabel = computed(() => props.options.find((o) => o.value === props.modelValue)?.label ?? '—')

function toggle() {
  if (open.value) { open.value = false; return }
  const r = btn.value!.getBoundingClientRect()
  pos.value = { left: r.left, top: r.bottom + 4, width: r.width }
  open.value = true
}
function pick(v: string | number) {
  emit('update:modelValue', v)
  open.value = false
}
const showGroup = (o: Opt, i: number) => !!o.group && o.group !== props.options[i - 1]?.group
</script>

<template>
  <div class="dd">
    <button ref="btn" type="button" class="dd-btn" @click="toggle">
      <span class="dd-label">{{ selectedLabel }}</span>
      <i class="dd-caret">▾</i>
    </button>
    <Teleport to="body">
      <div v-if="open" class="dd-back" @click="open = false" @contextmenu.prevent="open = false"></div>
      <div v-if="open" class="dd-menu" :style="{ left: `${pos.left}px`, top: `${pos.top}px`, width: `${pos.width}px` }">
        <template v-for="(o, i) in options" :key="`${o.value}`">
          <div v-if="showGroup(o, i)" class="dd-group">{{ o.group }}</div>
          <button type="button" class="dd-opt" :class="{ sel: o.value === modelValue }" @click="pick(o.value)">{{ o.label }}</button>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.dd{position:relative}
.dd-btn{width:100%;display:flex;align-items:center;justify-content:space-between;gap:8px;font:inherit;font-size:14px;
  color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;cursor:pointer;text-align:left}
.dd-btn:hover{border-color:var(--border-strong)}
.dd-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dd-caret{font-style:normal;font-size:10px;color:var(--text-faint);flex-shrink:0}
.dd-back{position:fixed;inset:0;z-index:1000}
.dd-menu{position:fixed;z-index:1001;max-height:300px;overflow:auto;background:var(--surface-2);border:1px solid var(--border);
  border-radius:var(--radius);box-shadow:0 8px 28px rgba(0,0,0,.45);padding:4px}
.dd-group{font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);padding:8px 8px 4px}
.dd-opt{display:block;width:100%;text-align:left;font:inherit;font-size:13px;color:var(--text);background:transparent;border:0;
  border-radius:var(--radius);padding:7px 9px;cursor:pointer}
.dd-opt:hover{background:var(--surface-3)}
.dd-opt.sel{background:var(--nav-active);color:var(--accent);font-weight:600}
</style>
