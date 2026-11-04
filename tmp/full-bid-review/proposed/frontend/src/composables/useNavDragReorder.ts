import { ref, type Ref } from 'vue'

export function useNavDragReorder<T extends { href: string }>(
  items: Ref<T[]>,
  onCommit: (hrefs: string[]) => void,
) {
  const dragFrom = ref<number | null>(null)
  const dragOver = ref<number | null>(null)
  const suppressClick = ref(false)

  function onDragStart(index: number, event: DragEvent) {
    dragFrom.value = index
    dragOver.value = index
    suppressClick.value = false
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move'
      event.dataTransfer.setData('text/plain', items.value[index]?.href ?? String(index))
    }
  }

  function onDragOver(index: number, event: DragEvent) {
    event.preventDefault()
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
    dragOver.value = index
    if (dragFrom.value != null && dragFrom.value !== index) {
      suppressClick.value = true
    }
  }

  function onDrop(index: number, event?: DragEvent) {
    event?.preventDefault()
    const from = dragFrom.value
    if (from == null || from === index) return
    const hrefs = items.value.map((item) => item.href)
    const next = [...hrefs]
    const [moved] = next.splice(from, 1)
    if (!moved) return
    next.splice(index, 0, moved)
    onCommit(next)
    suppressClick.value = true
  }

  function onDragEnd() {
    dragFrom.value = null
    dragOver.value = null
    window.setTimeout(() => {
      suppressClick.value = false
    }, 80)
  }

  function onClickCapture(event: Event) {
    if (!suppressClick.value) return
    event.preventDefault()
    event.stopPropagation()
  }

  function itemClass(index: number) {
    return {
      'is-dragging': dragFrom.value === index,
      'is-over': dragOver.value === index && dragFrom.value !== index,
    }
  }

  return {
    dragFrom,
    dragOver,
    onDragStart,
    onDragOver,
    onDrop,
    onDragEnd,
    onClickCapture,
    itemClass,
  }
}
