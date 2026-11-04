import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useMobileMenuStore = defineStore('mobile-menu', () => {
  const open = ref(false)

  function setOpen(v: boolean) {
    open.value = v
  }

  function toggle() {
    open.value = !open.value
  }

  function close() {
    open.value = false
  }

  watch(open, (v) => {
    document.body.style.overflow = v ? 'hidden' : ''
  })

  return { open, setOpen, toggle, close }
})
