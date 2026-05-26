## 2026-05-06 21:03:56 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/aiPlatform.ts`
- ` M packages/app/src/features/chat/ChatPage.tsx`
- ` M packages/app/src/features/model-arena/DetachedLaneRoute.tsx`
- ` M packages/app/src/features/model-arena/Lane.tsx`
- ` M packages/app/src/features/model-arena/MultiLaneShell.tsx`
- ` M packages/app/src/features/model-arena/__tests__/useLaneDnD.test.ts`
- ` M packages/app/src/features/model-arena/useLaneDnD.ts`
- ` M packages/app/src/features/shell/KeepAliveOutlet.tsx`
- ` M packages/app/src/router/index.tsx`
- ` M packages/app/src/store/__tests__/modelArena.test.ts`
- ` M packages/app/src/store/modelArena.ts`
- ` M packages/app/src/store/tabs.ts`
- `?? packages/app/src/store/arenaScope.tsx`

### Diff Stat

```text
 packages/api/src/aiPlatform.ts                     |  30 ++-
 packages/app/src/features/chat/ChatPage.tsx        |  21 +-
 .../src/features/model-arena/DetachedLaneRoute.tsx |  40 ++--
 packages/app/src/features/model-arena/Lane.tsx     |  16 +-
 .../src/features/model-arena/MultiLaneShell.tsx    |  16 +-
 .../model-arena/__tests__/useLaneDnD.test.ts       |  34 ++--
 .../app/src/features/model-arena/useLaneDnD.ts     |  15 +-
 .../app/src/features/shell/KeepAliveOutlet.tsx     |   5 +-
 packages/app/src/router/index.tsx                  | 119 +++++++++++-
 .../app/src/store/__tests__/modelArena.test.ts     | 171 ++++++++++-------
 packages/app/src/store/modelArena.ts               | 211 ++++++++++++++-------
 packages/app/src/store/tabs.ts                     |  26 ++-
 12 files changed, 487 insertions(+), 217 deletions(-)
```

## 2026-05-06 20:01:56 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/web/index.html`
- ` M apps/web/vite.config.ts`
- ` M packages/app/src/features/shell/Sidebar.tsx`

### Diff Stat

```text
 apps/web/index.html                         | 22 +++++++++++-----------
 apps/web/vite.config.ts                     |  2 ++
 packages/app/src/features/shell/Sidebar.tsx |  9 +++------
 3 files changed, 16 insertions(+), 17 deletions(-)
```

## 2026-05-06 07:52:50 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ChatWindow.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/store/composer.ts`
- ` M packages/app/src/store/modelArena.ts`

### Diff Stat

```text
 packages/app/src/features/chat/ChatWindow.tsx      | 30 ++++++++++++++++++-
 .../src/features/composer/ComposerModelPill.tsx    | 15 +++++++++-
 packages/app/src/store/composer.ts                 | 34 ++++++++++++++++++++--
 packages/app/src/store/modelArena.ts               |  9 ++++++
 4 files changed, 84 insertions(+), 4 deletions(-)
```

## 2026-05-06 07:45:05 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ChatPage.tsx`
- ` M packages/app/src/features/model-arena/MultiLaneShell.tsx`

### Diff Stat

```text
 packages/app/src/features/chat/ChatPage.tsx        | 83 ++++++++++++----------
 .../src/features/model-arena/MultiLaneShell.tsx    | 29 ++------
 2 files changed, 49 insertions(+), 63 deletions(-)
```

## 2026-05-06 07:40:45 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ChatWindow.tsx`
- ` M packages/app/src/features/chat/ConversationView.tsx`
- ` M packages/app/src/features/model-arena/Lane.tsx`

### Diff Stat

```text
 packages/app/src/features/chat/ChatWindow.tsx      |  8 +++++-
 .../app/src/features/chat/ConversationView.tsx     | 25 ++++++++++++++++
 packages/app/src/features/model-arena/Lane.tsx     | 33 +++++++++++++++++++++-
 3 files changed, 64 insertions(+), 2 deletions(-)
```

## 2026-05-06 07:32:48 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ChatPage.tsx`
- ` M packages/app/src/features/chat/Composer.tsx`
- ` M packages/app/src/features/chat/ConversationView.tsx`
- ` M packages/app/src/features/chat/ModelPicker.tsx`
- ` M packages/app/src/features/chat/useAttachments.ts`
- ` M packages/app/src/features/chat/useSlashMention.ts`
- ` M packages/app/src/features/composer/CapabilityPickers.tsx`
- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/composer/ComposerEmbedPill.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/features/composer/KnowledgePickerPill.tsx`
- ` M packages/app/src/features/composer/SearchModePill.tsx`
- ` M packages/app/src/features/model-arena/DetachedLaneRoute.tsx`
- ` M packages/app/src/features/model-arena/Lane.tsx`
- ` M packages/app/src/features/model-arena/MultiLaneShell.tsx`
- ` M packages/app/src/store/composer.ts`
- `?? packages/app/src/features/chat/ChatWindow.tsx`

### Diff Stat

```text
 packages/app/src/features/chat/ChatPage.tsx        |   9 +-
 packages/app/src/features/chat/Composer.tsx        |  10 +-
 .../app/src/features/chat/ConversationView.tsx     |  15 +-
 packages/app/src/features/chat/ModelPicker.tsx     |   8 +-
 packages/app/src/features/chat/useAttachments.ts   |  21 +-
 packages/app/src/features/chat/useSlashMention.ts  |  23 +-
 .../src/features/composer/CapabilityPickers.tsx    |  10 +-
 .../app/src/features/composer/ChatComposer.tsx     |  14 +-
 .../src/features/composer/ComposerEmbedPill.tsx    |   6 +-
 .../src/features/composer/ComposerModelPill.tsx    |   8 +-
 .../src/features/composer/KnowledgePickerPill.tsx  |   6 +-
 .../app/src/features/composer/SearchModePill.tsx   |   6 +-
 .../src/features/model-arena/DetachedLaneRoute.tsx |  24 +-
 packages/app/src/features/model-arena/Lane.tsx     | 342 ++++-----------------
 .../src/features/model-arena/MultiLaneShell.tsx    |  13 +-
 packages/app/src/store/composer.ts                 | 129 ++++++--
 16 files changed, 238 insertions(+), 406 deletions(-)
```

## 2026-05-06 07:13:20 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ChatPage.tsx`
- ` M packages/app/src/features/chat/ModelPicker.tsx`
- ` M packages/app/src/router/index.tsx`
- `?? packages/app/src/features/model-arena/DetachedLaneRoute.tsx`
- `?? packages/app/src/features/model-arena/Lane.tsx`
- `?? packages/app/src/features/model-arena/MultiLaneShell.tsx`
- `?? packages/app/src/features/model-arena/__tests__/useLaneDnD.test.ts`
- `?? packages/app/src/features/model-arena/useLaneDnD.ts`
- `?? packages/app/src/store/__tests__/modelArena.test.ts`
- `?? packages/app/src/store/modelArena.ts`

### Diff Stat

```text
 packages/app/src/features/chat/ChatPage.tsx    | 55 +++++++++++++++++++++++++-
 packages/app/src/features/chat/ModelPicker.tsx | 21 ++++++++--
 packages/app/src/router/index.tsx              | 19 +++++++++
 3 files changed, 90 insertions(+), 5 deletions(-)
```

## 2026-05-05 22:24:30 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/kbUniverse.ts`
- `?? packages/app/src/features/kb/collections/CreateCollectionDialog.tsx`
- `?? packages/app/src/features/kb/collections/KbCollectionCard.tsx`
- `?? packages/app/src/features/kb/collections/KbCollectionsPage.tsx`
- `?? packages/app/src/features/kb/collections/ManageMembersDialog.tsx`
- `?? packages/app/src/features/kb/collections/__tests__/splitByMime.test.ts`
- `?? packages/app/src/features/kb/collections/splitByMime.ts`
- `?? packages/app/src/features/kb/folder-sync/FolderSyncJobDialog.tsx`
- `?? packages/app/src/features/kb/folder-sync/FolderSyncPage.tsx`

### Diff Stat

```text
 packages/api/src/kbUniverse.ts | 244 +++++++++++++++++++++++++++++++++++++++++
 1 file changed, 244 insertions(+)
```

## 2026-05-05 13:55:58 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/app/src/features/composer/ModelMenuList.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      |  6 ++++
 .../app/src/features/composer/ModelMenuList.tsx    | 32 ++++++++++++++++------
 2 files changed, 29 insertions(+), 9 deletions(-)
```

## 2026-05-05 13:34:33 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ModelPicker.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/features/composer/ModelMenuList.tsx`

### Diff Stat

```text
 packages/app/src/features/chat/ModelPicker.tsx     |  7 +++-
 .../src/features/composer/ComposerModelPill.tsx    |  7 +++-
 .../app/src/features/composer/ModelMenuList.tsx    | 41 ++++++++++++++++++----
 3 files changed, 47 insertions(+), 8 deletions(-)
```

## 2026-05-05 13:16:56 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ModelPicker.tsx`
- ` M packages/app/src/features/chat/buildRequest.ts`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/store/composer.ts`

### Diff Stat

```text
 packages/app/src/features/chat/ModelPicker.tsx         |  7 +++++--
 packages/app/src/features/chat/buildRequest.ts         |  8 +++++++-
 .../app/src/features/composer/ComposerModelPill.tsx    |  3 ++-
 packages/app/src/store/composer.ts                     | 18 ++++++++++++++++--
 4 files changed, 30 insertions(+), 6 deletions(-)
```

## 2026-05-05 12:55:59 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/web/src/main.tsx`

### Diff Stat

```text
 apps/web/src/main.tsx | 4 ++++
 1 file changed, 4 insertions(+)
```

## 2026-05-03 20:20:06 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/kbUniverse.ts`
- ` M packages/app/src/features/kb/KbAskResultPanel.tsx`
- ` M packages/app/src/features/kb/KbBoard.tsx`

### Diff Stat

```text
 packages/api/src/kbUniverse.ts                    |  10 ++
 packages/app/src/features/kb/KbAskResultPanel.tsx | 136 +++++++++++++++++++---
 packages/app/src/features/kb/KbBoard.tsx          |  23 ++++
 3 files changed, 154 insertions(+), 15 deletions(-)
```

## 2026-05-03 19:46:31 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/dataMounts.ts`

### Diff Stat

```text
 packages/api/src/dataMounts.ts | 15 +++++++++++----
 1 file changed, 11 insertions(+), 4 deletions(-)
```

## 2026-05-03 19:20:32 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/kbResults.ts`

### Diff Stat

```text
 packages/api/src/kbResults.ts | 27 ++++++++++++++++++++++++++-
 1 file changed, 26 insertions(+), 1 deletion(-)
```

## 2026-05-03 18:59:24 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/index.ts`
- ` M packages/app/src/features/chat/MessageBubble.tsx`
- ` M packages/app/src/features/kb/KbAskResultPanel.tsx`
- ` M packages/app/src/features/kb/shared/KbResultBlock.tsx`
- `?? packages/api/src/kbResults.ts`
- `?? packages/app/src/features/kb/components/KbResultsView.tsx`

### Diff Stat

```text
 packages/api/src/index.ts                          |   1 +
 packages/app/src/features/chat/MessageBubble.tsx   | 203 +---------
 packages/app/src/features/kb/KbAskResultPanel.tsx  | 425 ++-------------------
 .../app/src/features/kb/shared/KbResultBlock.tsx   | 375 ++----------------
 4 files changed, 55 insertions(+), 949 deletions(-)
```

## 2026-05-03 18:09:37 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/kbUniverse.ts`
- ` M packages/transport/src/types.ts`

### Diff Stat

```text
 packages/api/src/kbUniverse.ts  | 86 +++++++++++++++++++++++++++++++++++++++++
 packages/transport/src/types.ts |  3 ++
 2 files changed, 89 insertions(+)
```

## 2026-05-02 23:03:49 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/web/vite.config.ts`
- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/annotation/data-mounts/MountWizard.tsx`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- ` M packages/app/src/features/marketplace/components/VendorHeroStrip.tsx`

### Diff Stat

```text
 apps/web/vite.config.ts                            |   4 +
 packages/api/src/imageModels.ts                    |  19 +++-
 .../annotation/data-mounts/MountWizard.tsx         |   7 +-
 .../src/features/marketplace/MarketplacePage.tsx   |  52 ++++++++-
 .../marketplace/components/VendorHeroStrip.tsx     | 122 +++++++++++++--------
 5 files changed, 150 insertions(+), 54 deletions(-)
```

## 2026-05-02 21:59:02 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/annotation/data-mounts/MountWizard.tsx`

### Diff Stat

```text
 .../annotation/data-mounts/MountWizard.tsx         | 252 ++++++++++++++++++++-
 1 file changed, 243 insertions(+), 9 deletions(-)
```

## 2026-05-02 21:48:15 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/aiPlatform/RuntimeCenter.tsx`
- ` M packages/app/src/features/annotation/data-mounts/DataMountsPanel.tsx`
- ` M packages/app/src/features/annotation/data-mounts/MountWizard.tsx`
- ` M packages/app/src/features/marketplace/components/VendorHeroStrip.tsx`
- `?? docs/dialog-patterns.md`
- `?? packages/app/src/stories/MountWizard.stories.tsx`

### Diff Stat

```text
 .../app/src/features/aiPlatform/RuntimeCenter.tsx  |  34 ++---
 .../annotation/data-mounts/DataMountsPanel.tsx     | 139 ++++++++++++++++++++-
 .../annotation/data-mounts/MountWizard.tsx         |  33 +++--
 .../marketplace/components/VendorHeroStrip.tsx     |  69 +++++-----
 4 files changed, 212 insertions(+), 63 deletions(-)
```

## 2026-05-02 20:56:11 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/dataMounts.ts`
- ` M packages/app/src/features/kb/detail/KbDetailPage.tsx`
- `?? packages/app/src/features/kb/detail/PendingMountsBanner.tsx`
- `?? packages/app/src/stories/DataMountsPanel.stories.tsx`

### Diff Stat

```text
 packages/api/src/dataMounts.ts                     | 36 ++++++++++++++++++++++
 .../app/src/features/kb/detail/KbDetailPage.tsx    |  5 +++
 2 files changed, 41 insertions(+)
```

## 2026-05-02 20:46:23 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/index.ts`
- ` M packages/app/src/features/annotation/AnnotationPage.tsx`
- `?? packages/api/src/dataMounts.ts`
- `?? packages/app/src/features/annotation/data-mounts/DataMountsPanel.tsx`
- `?? packages/app/src/features/annotation/data-mounts/MountDetailDrawer.tsx`
- `?? packages/app/src/features/annotation/data-mounts/MountWizard.tsx`

### Diff Stat

```text
 packages/api/src/index.ts                          |  1 +
 .../app/src/features/annotation/AnnotationPage.tsx | 22 +++++++++++++++++++---
 2 files changed, 20 insertions(+), 3 deletions(-)
```

## 2026-05-02 20:03:09 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- `?? packages/app/src/stories/GuidanceCard.stories.tsx`

### Diff Stat

```text
```

## 2026-05-02 17:59:08 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/aiPlatform.ts`
- ` M packages/app/src/features/aiPlatform/CapabilityCenter.tsx`

### Diff Stat

```text
 packages/api/src/aiPlatform.ts                     |  43 +++++
 .../src/features/aiPlatform/CapabilityCenter.tsx   | 197 +++++++++++++++++++--
 2 files changed, 223 insertions(+), 17 deletions(-)
```

## 2026-05-02 17:50:52 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/aiPlatform.ts`
- ` M packages/app/src/features/aiPlatform/AiPlatformPanel.tsx`
- ` M packages/app/src/features/chat/useAttachments.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- ` M packages/app/src/features/marketplace/components/MarketplaceModelTable.tsx`
- `?? packages/app/src/features/aiPlatform/CapabilityCenter.tsx`

### Diff Stat

```text
 packages/api/src/aiPlatform.ts                     | 190 +++++++++++++++++
 .../src/features/aiPlatform/AiPlatformPanel.tsx    | 236 ++++++++++++++++++---
 packages/app/src/features/chat/useAttachments.ts   |  44 +++-
 .../src/features/marketplace/MarketplacePage.tsx   |  35 ++-
 .../components/MarketplaceModelTable.tsx           |  46 +++-
 5 files changed, 501 insertions(+), 50 deletions(-)
```

## 2026-05-02 17:15:46 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/aiPlatform.ts`
- ` M packages/app/src/features/aiPlatform/AiPlatformPanel.tsx`

### Diff Stat

```text
 packages/api/src/aiPlatform.ts                     |  49 +++++
 .../src/features/aiPlatform/AiPlatformPanel.tsx    | 212 ++++++++++++++++++++-
 2 files changed, 260 insertions(+), 1 deletion(-)
```

## 2026-05-02 17:09:08 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/desktop/package.json`
- ` M apps/desktop/src/main.tsx`
- ` M apps/web/src/main.tsx`
- ` M e2e/helpers/mockBackend.ts`
- ` M packages/app/src/Shell.tsx`
- ` M packages/app/src/features/aiPlatform/AiPlatformPanel.tsx`
- `?? apps/desktop/src-tauri/tauri.thin.conf.json`
- `?? e2e/07-thin-client-login.spec.ts`
- `?? packages/app/src/features/auth/ServerLoginModal.tsx`
- `?? packages/app/src/store/thinClient.ts`
- `?? packages/app/src/stories/AiPlatformPanel.stories.tsx`
- `?? packages/app/src/stories/ServerLoginModal.stories.tsx`

### Diff Stat

```text
 apps/desktop/package.json                          |  3 ++
 apps/desktop/src/main.tsx                          | 15 ++++++-
 apps/web/src/main.tsx                              | 14 +++++++
 e2e/helpers/mockBackend.ts                         | 12 ++++++
 packages/app/src/Shell.tsx                         | 48 +++++++++++++++++++++-
 .../src/features/aiPlatform/AiPlatformPanel.tsx    |  6 +--
 6 files changed, 92 insertions(+), 6 deletions(-)
```

## 2026-05-02 16:26:27 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/index.ts`
- ` M packages/app/src/features/admin/AdminPage.tsx`
- ` M packages/app/src/features/settings/SettingsDialog.tsx`
- `?? packages/api/src/aiPlatform.ts`
- `?? packages/api/src/runtime.ts`
- `?? packages/app/src/features/admin/SystemServicesPage.tsx`
- `?? packages/app/src/features/aiPlatform/AiPlatformPanel.tsx`
- `?? packages/app/src/features/aiPlatform/index.ts`

### Diff Stat

```text
 packages/api/src/index.ts                          |  2 ++
 packages/app/src/features/admin/AdminPage.tsx      |  7 +++--
 .../app/src/features/settings/SettingsDialog.tsx   | 30 +++++++++++++++++++---
 3 files changed, 34 insertions(+), 5 deletions(-)
```

## 2026-05-02 11:47:35 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- `?? docs/plans/local-model-runtime-gateway.md`

### Diff Stat

```text
```

## 2026-05-02 11:06:02 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- ` M packages/app/src/features/marketplace/components/MarketplaceModelTable.tsx`

### Diff Stat

```text
 packages/api/src/imageModels.ts                    |  42 +++++++-
 .../src/features/marketplace/MarketplacePage.tsx   | 114 ++++++++++++++++-----
 .../components/MarketplaceModelTable.tsx           |  12 +++
 3 files changed, 139 insertions(+), 29 deletions(-)
```

## 2026-05-02 10:42:48 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`

### Diff Stat

```text
 packages/api/src/imageModels.ts | 21 +++++++++++++++++++++
 1 file changed, 21 insertions(+)
```

## 2026-05-02 10:36:39 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`

### Diff Stat

```text
 packages/api/src/imageModels.ts                    |   2 +-
 .../src/features/marketplace/MarketplacePage.tsx   | 127 ++-------------------
 2 files changed, 9 insertions(+), 120 deletions(-)
```

## 2026-05-02 10:30:42 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`

### Diff Stat

```text
 packages/api/src/imageModels.ts                    | 37 ++++++++++++++++++-
 .../src/features/marketplace/MarketplacePage.tsx   | 43 +++++++++++++++++++++-
 2 files changed, 77 insertions(+), 3 deletions(-)
```

## 2026-05-02 10:13:16 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`

### Diff Stat

```text
 packages/api/src/imageModels.ts                    | 16 ++----
 .../src/features/marketplace/MarketplacePage.tsx   | 66 ++++++++++++++++------
 2 files changed, 56 insertions(+), 26 deletions(-)
```

## 2026-05-02 10:01:22 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/web/vite.config.ts`

### Diff Stat

```text
 apps/web/vite.config.ts | 1 +
 1 file changed, 1 insertion(+)
```

## 2026-05-02 09:59:18 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`

### Diff Stat

```text
 packages/api/src/imageModels.ts                    |  3 +-
 .../src/features/marketplace/MarketplacePage.tsx   | 65 ++++++++++++++++++++--
 2 files changed, 62 insertions(+), 6 deletions(-)
```

## 2026-05-02 09:56:48 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- `?? docs/plans/universal-crawler-llm-cleaning.md`

### Diff Stat

```text
 packages/api/src/imageModels.ts                    | 33 +++++++++++++++++++++-
 .../src/features/marketplace/MarketplacePage.tsx   | 10 ++++++-
 2 files changed, 41 insertions(+), 2 deletions(-)
```

## 2026-05-02 09:50:14 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`

### Diff Stat

```text
 packages/api/src/imageModels.ts                    | 29 ++++++++++------------
 .../src/features/marketplace/MarketplacePage.tsx   | 11 ++++++--
 2 files changed, 22 insertions(+), 18 deletions(-)
```

## 2026-05-02 09:42:46 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- ` M packages/app/src/features/marketplace/components/MarketplaceModelTable.tsx`

### Diff Stat

```text
 .../src/features/marketplace/MarketplacePage.tsx   |  72 ++++++-
 .../components/MarketplaceModelTable.tsx           | 231 ++++++++++++---------
 2 files changed, 196 insertions(+), 107 deletions(-)
```

## 2026-05-02 09:32:00 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/imageModels.ts`

### Diff Stat

```text
 packages/api/src/imageModels.ts | 28 ++++++++++++++++++----------
 1 file changed, 18 insertions(+), 10 deletions(-)
```

## 2026-05-02 09:22:15 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M docs/plans/model-marketplace-redesign.md`

### Diff Stat

```text
 docs/plans/model-marketplace-redesign.md | 9 ++++++---
 1 file changed, 6 insertions(+), 3 deletions(-)
```

## 2026-05-02 09:19:59 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/api/src/index.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- `?? docs/plans/model-marketplace-redesign.md`
- `?? packages/api/src/imageModels.ts`
- `?? packages/app/src/features/marketplace/components/MarketplaceModelTable.tsx`
- `?? packages/app/src/features/marketplace/components/VendorHeroStrip.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      |  95 ---
 packages/api/src/index.ts                          |   1 +
 .../src/features/marketplace/MarketplacePage.tsx   | 683 +++++++++++----------
 3 files changed, 364 insertions(+), 415 deletions(-)
```

## 2026-05-02 08:31:31 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- `?? packages/app/src/features/marketplace/components/ImageEmbeddingModelsCard.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      | 95 ++++++++++++++++++++++
 .../src/features/marketplace/MarketplacePage.tsx   | 17 ++--
 2 files changed, 107 insertions(+), 5 deletions(-)
```

## 2026-05-01 20:27:02 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/components/AssistantBrandLogo.tsx`
- ` M packages/app/src/features/auth/LoginModal.tsx`
- ` M packages/app/src/features/chat/ConversationView.tsx`
- ` M packages/app/src/features/home/HomePage.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/components/AssistantBrandLogo.tsx |  22 ++--
 packages/app/src/features/auth/LoginModal.tsx      |   2 +-
 .../app/src/features/chat/ConversationView.tsx     |   2 +-
 packages/app/src/features/home/HomePage.tsx        |   2 +-
 .../app/src/features/office/OfficeEditorPage.tsx   | 131 +++++++++++++++------
 5 files changed, 110 insertions(+), 49 deletions(-)
```

## 2026-05-01 20:16:06 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/marketplace/MarketplacePage.tsx`
- ` M packages/app/src/features/marketplace/components/ProviderCard.tsx`

### Diff Stat

```text
 .../src/features/marketplace/MarketplacePage.tsx   | 37 ++--------------------
 .../marketplace/components/ProviderCard.tsx        | 12 +++----
 2 files changed, 8 insertions(+), 41 deletions(-)
```

## 2026-05-01 19:44:53 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/api/src/office.ts                         | 97 +++++++++++++++++++++-
 .../app/src/features/office/OfficeEditorPage.tsx   | 64 ++++++--------
 2 files changed, 123 insertions(+), 38 deletions(-)
```

## 2026-05-01 19:39:03 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/auth/LoginModal.tsx`
- ` M packages/app/src/features/kb/AddStructuredSourceDialog.tsx`
- ` M packages/app/src/features/kb/KbAdminMenu.tsx`
- ` M packages/app/src/features/kb/KbDetailDialog.tsx`
- ` M packages/app/src/features/kb/SchemaScopePicker.tsx`
- ` M packages/app/src/features/kb/sync/RemoteSyncDialog.tsx`
- ` M packages/app/src/features/marketplace/components/PlatformCreateDialog.tsx`
- ` M packages/app/src/features/marketplace/components/PlatformSettingsDialog.tsx`
- ` M packages/app/src/features/mcp/McpEditDialog.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/dialogs/CreateDocDialog.tsx`
- ` M packages/app/src/features/office/dialogs/MoveToGroupDialog.tsx`
- ` M packages/app/src/features/office/dialogs/RenameDialog.tsx`
- ` M packages/app/src/features/office/dialogs/ShareDialog.tsx`
- ` M packages/app/src/features/space/AnnotateDialog.tsx`
- ` M packages/app/src/features/space/ImportExportDialog.tsx`
- ` M packages/app/src/features/space/PublishDialog.tsx`
- ` M packages/app/src/features/space/ShareDialog.tsx`
- `?? packages/app/src/components/AssistantBrandLogo.tsx`

### Diff Stat

```text
 packages/app/src/features/auth/LoginModal.tsx      | 14 +++--
 .../src/features/kb/AddStructuredSourceDialog.tsx  |  7 ++-
 packages/app/src/features/kb/KbAdminMenu.tsx       |  6 +-
 packages/app/src/features/kb/KbDetailDialog.tsx    |  3 +-
 packages/app/src/features/kb/SchemaScopePicker.tsx |  7 ++-
 .../app/src/features/kb/sync/RemoteSyncDialog.tsx  |  9 +--
 .../components/PlatformCreateDialog.tsx            |  5 +-
 .../components/PlatformSettingsDialog.tsx          | 11 ++--
 packages/app/src/features/mcp/McpEditDialog.tsx    |  7 ++-
 .../app/src/features/office/OfficeEditorPage.tsx   | 65 ++++++++++++++++++----
 .../features/office/dialogs/CreateDocDialog.tsx    |  8 ++-
 .../features/office/dialogs/MoveToGroupDialog.tsx  |  8 ++-
 .../src/features/office/dialogs/RenameDialog.tsx   |  8 ++-
 .../src/features/office/dialogs/ShareDialog.tsx    | 12 +++-
 packages/app/src/features/space/AnnotateDialog.tsx |  8 ++-
 .../app/src/features/space/ImportExportDialog.tsx  | 15 ++++-
 packages/app/src/features/space/PublishDialog.tsx  |  5 +-
 packages/app/src/features/space/ShareDialog.tsx    |  8 ++-
 18 files changed, 155 insertions(+), 51 deletions(-)
```

## 2026-05-01 19:19:37 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 packages/app/src/features/office/editorBridge.ts | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

## 2026-05-01 18:32:46 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 packages/app/src/features/office/editorBridge.ts | 9 +++++----
 1 file changed, 5 insertions(+), 4 deletions(-)
```

## 2026-05-01 18:23:04 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/auth/LoginModal.tsx`
- ` M packages/app/src/features/chat/ConversationView.tsx`
- ` M packages/app/src/features/home/HomePage.tsx`
- `?? packages/app/src/lib/brandAssets.ts`

### Diff Stat

```text
 packages/app/src/features/auth/LoginModal.tsx       | 5 ++---
 packages/app/src/features/chat/ConversationView.tsx | 3 ++-
 packages/app/src/features/home/HomePage.tsx         | 3 ++-
 3 files changed, 6 insertions(+), 5 deletions(-)
```

## 2026-05-01 18:10:13 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/auth/LoginModal.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`
- ` M packages/i18n/src/locales/zh-CN.ts`
- `?? packages/app/src/images/logo.png`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           | 14 ++++++++++++
 packages/app/src/features/auth/LoginModal.tsx      | 26 +++++++++++++---------
 .../app/src/features/office/OfficeEditorPage.tsx   |  1 +
 packages/app/src/features/office/editorBridge.ts   |  3 +++
 packages/i18n/src/locales/zh-CN.ts                 |  4 ++--
 5 files changed, 35 insertions(+), 13 deletions(-)
```

## 2026-05-01 17:54:16 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           | 20 +++-----
 .../app/src/features/office/OfficeEditorPage.tsx   | 23 +++++++--
 packages/app/src/features/office/editorBridge.ts   | 58 ++++++----------------
 3 files changed, 39 insertions(+), 62 deletions(-)
```

## 2026-05-01 17:31:33 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           | 56 +++++++++++++++++++++-
 packages/api/src/office.ts                         |  2 +-
 .../app/src/features/composer/ChatComposer.tsx     |  8 +---
 .../app/src/features/office/OfficeEditorPage.tsx   | 24 ++++++----
 packages/app/src/features/office/editorBridge.ts   | 28 ++++++++++-
 5 files changed, 97 insertions(+), 21 deletions(-)
```

## 2026-05-01 17:19:01 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 19 +++++++++++++++++--
 1 file changed, 17 insertions(+), 2 deletions(-)
```

## 2026-05-01 17:17:50 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/ModelPicker.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/features/kb/detail/StructuredKbDetail.tsx`

### Diff Stat

```text
 packages/app/src/features/chat/ModelPicker.tsx     | 14 ++---
 .../src/features/composer/ComposerModelPill.tsx    |  5 +-
 .../src/features/kb/detail/StructuredKbDetail.tsx  | 63 ++++++++++++++++++++--
 3 files changed, 69 insertions(+), 13 deletions(-)
```

## 2026-05-01 17:11:49 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/composer/SearchModePill.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/api/src/office.ts                         |  18 +++-
 .../app/src/features/composer/ChatComposer.tsx     |   4 +-
 .../app/src/features/composer/SearchModePill.tsx   |   2 +-
 .../app/src/features/office/OfficeEditorPage.tsx   | 110 +++++++++++++--------
 4 files changed, 90 insertions(+), 44 deletions(-)
```

## 2026-05-01 17:05:26 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficePage.tsx`
- ` M packages/app/src/features/office/hooks/useVectorizeStream.ts`

### Diff Stat

```text
 packages/app/src/features/office/OfficePage.tsx              | 7 ++++---
 packages/app/src/features/office/hooks/useVectorizeStream.ts | 9 +++++++--
 2 files changed, 11 insertions(+), 5 deletions(-)
```

## 2026-05-01 17:02:15 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js         | 153 ++++++++++++++++++++++-
 packages/app/src/features/office/editorBridge.ts |  43 ++++++-
 2 files changed, 189 insertions(+), 7 deletions(-)
```

## 2026-05-01 16:54:54 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 19 +++++++++----------
 1 file changed, 9 insertions(+), 10 deletions(-)
```

## 2026-05-01 16:54:02 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js         | 35 ++++++++++++++++++++++--
 packages/app/src/features/office/editorBridge.ts | 28 +++++++++++++------
 2 files changed, 51 insertions(+), 12 deletions(-)
```

## 2026-05-01 16:45:09 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 packages/api/src/office.ts                            | 12 ++++++++++++
 packages/app/src/features/office/OfficeEditorPage.tsx | 10 ++++++++++
 packages/app/src/features/office/editorBridge.ts      | 15 +++++++++++----
 3 files changed, 33 insertions(+), 4 deletions(-)
```

## 2026-05-01 16:23:55 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/client.ts`
- ` M packages/api/src/office.ts`

### Diff Stat

```text
 packages/api/src/client.ts | 2 +-
 packages/api/src/office.ts | 2 +-
 2 files changed, 2 insertions(+), 2 deletions(-)
```

## 2026-05-01 16:18:31 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 16 ++++++++++++++--
 1 file changed, 14 insertions(+), 2 deletions(-)
```

## 2026-05-01 16:16:12 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 17 +++++++++++++++++
 1 file changed, 17 insertions(+)
```

## 2026-05-01 16:10:44 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 6 +++++-
 1 file changed, 5 insertions(+), 1 deletion(-)
```

## 2026-05-01 15:59:52 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 114 +++++++++++++--------
 1 file changed, 71 insertions(+), 43 deletions(-)
```

## 2026-05-01 15:58:38 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/api/src/office.ts                         |  9 ++++++--
 .../app/src/features/office/OfficeEditorPage.tsx   | 26 +++++++++++++++++++---
 2 files changed, 30 insertions(+), 5 deletions(-)
```

## 2026-05-01 15:46:01 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 31 +++++++++++++++++++++-
 1 file changed, 30 insertions(+), 1 deletion(-)
```

## 2026-05-01 15:42:05 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           | 58 +++++++++++++++++++---
 .../app/src/features/office/OfficeEditorPage.tsx   | 44 ++++++++++++++--
 2 files changed, 93 insertions(+), 9 deletions(-)
```

## 2026-05-01 15:14:49 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           |  64 +++++---
 .../app/src/features/composer/ChatComposer.tsx     |   4 +
 .../app/src/features/office/OfficeEditorPage.tsx   | 177 ++++++++++++++++++++-
 packages/app/src/features/office/editorBridge.ts   |  26 ++-
 4 files changed, 244 insertions(+), 27 deletions(-)
```

## 2026-05-01 15:12:12 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 5 ++++-
 1 file changed, 4 insertions(+), 1 deletion(-)
```

## 2026-05-01 15:06:46 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 16 +++++++++++++++-
 1 file changed, 15 insertions(+), 1 deletion(-)
```

## 2026-05-01 14:53:38 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           |  52 +++++
 .../app/src/features/office/OfficeEditorPage.tsx   | 211 +++++++++++++--------
 packages/app/src/features/office/editorBridge.ts   |   4 +-
 3 files changed, 185 insertions(+), 82 deletions(-)
```

## 2026-05-01 14:37:03 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js | 7 +++++--
 1 file changed, 5 insertions(+), 2 deletions(-)
```

## 2026-05-01 14:36:22 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js | 40 ++++++++++++++++++++++++++------
 1 file changed, 33 insertions(+), 7 deletions(-)
```

## 2026-05-01 14:28:15 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- `?? onlyoffice-plugin/chayuan-office/translations/en.json`
- `?? onlyoffice-plugin/chayuan-office/translations/langs.json`
- `?? onlyoffice-plugin/chayuan-office/translations/zh-CN.json`

### Diff Stat

```text
```

## 2026-05-01 13:11:48 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/kb/detail/StructuredKbDetail.tsx`
- ` M packages/app/src/features/kb/detail/VectorKbDetail.tsx`

### Diff Stat

```text
 packages/app/src/features/kb/detail/StructuredKbDetail.tsx | 6 +++---
 packages/app/src/features/kb/detail/VectorKbDetail.tsx     | 6 +++---
 2 files changed, 6 insertions(+), 6 deletions(-)
```

## 2026-05-01 13:06:33 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/annotation/AnnotationPage.tsx`
- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/features/composer/KnowledgePickerPill.tsx`

### Diff Stat

```text
 .../app/src/features/annotation/AnnotationPage.tsx | 63 +++-------------------
 .../app/src/features/composer/ChatComposer.tsx     | 16 +++---
 .../src/features/composer/ComposerModelPill.tsx    | 11 ++--
 .../src/features/composer/KnowledgePickerPill.tsx  |  9 +++-
 4 files changed, 30 insertions(+), 69 deletions(-)
```

## 2026-05-01 12:52:00 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/kb/KbBoard.tsx`

### Diff Stat

```text
 packages/app/src/features/kb/KbBoard.tsx | 9 ++-------
 1 file changed, 2 insertions(+), 7 deletions(-)
```

## 2026-05-01 12:45:23 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/annotation/AnnotationPage.tsx`

### Diff Stat

```text
 .../app/src/features/annotation/AnnotationPage.tsx | 297 ++++++++++++++++++++-
 1 file changed, 285 insertions(+), 12 deletions(-)
```

## 2026-05-01 12:41:24 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/annotation.ts`
- ` M packages/app/src/features/annotation/AnnotationPage.tsx`
- ` M packages/app/src/features/chat/MessageBubble.tsx`
- ` M packages/app/src/features/chat/useChayuanChat.ts`
- ` M packages/transport/src/__tests__/sse-parser.test.ts`
- ` M packages/transport/src/sse-parser.ts`
- ` M packages/transport/src/types.ts`

### Diff Stat

```text
 packages/api/src/annotation.ts                     | 93 ++++++++++++++++++++++
 .../app/src/features/annotation/AnnotationPage.tsx | 60 +++++++++++++-
 packages/app/src/features/chat/MessageBubble.tsx   | 50 +++++++++++-
 packages/app/src/features/chat/useChayuanChat.ts   | 10 +++
 .../transport/src/__tests__/sse-parser.test.ts     | 28 +++++++
 packages/transport/src/sse-parser.ts               | 34 ++++++++
 packages/transport/src/types.ts                    | 18 +++++
 7 files changed, 291 insertions(+), 2 deletions(-)
```

## 2026-05-01 12:22:06 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/annotation/AnnotationPage.tsx`
- ` M packages/app/src/features/kb/KbAskResultPanel.tsx`
- ` M packages/app/src/features/shell/Sidebar.tsx`
- ` M packages/app/src/features/shell/page-registry.tsx`
- ` M packages/app/src/features/space/AnnotateDialog.tsx`
- ` M packages/app/src/features/space/AppRuntimePage.tsx`
- ` M packages/app/src/features/space/ChatRuntime.tsx`
- ` M packages/app/src/features/space/TestSuiteSection.tsx`

### Diff Stat

```text
 .../app/src/features/annotation/AnnotationPage.tsx | 123 ++++++++++++++++-----
 packages/app/src/features/kb/KbAskResultPanel.tsx  |   2 +-
 packages/app/src/features/shell/Sidebar.tsx        |   2 +-
 packages/app/src/features/shell/page-registry.tsx  |   2 +-
 packages/app/src/features/space/AnnotateDialog.tsx |  16 +--
 packages/app/src/features/space/AppRuntimePage.tsx |   2 +-
 packages/app/src/features/space/ChatRuntime.tsx    |   2 +-
 .../app/src/features/space/TestSuiteSection.tsx    |   4 +-
 8 files changed, 111 insertions(+), 42 deletions(-)
```

## 2026-05-01 12:09:31 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/config.json`
- ` M packages/api/src/kbUniverse.ts`
- ` M packages/app/src/features/kb/create/VectorKbForm.tsx`
- ` M packages/app/src/features/kb/detail/VectorKbDetail.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/config.json       |   2 +
 packages/api/src/kbUniverse.ts                     |  38 +-
 .../app/src/features/kb/create/VectorKbForm.tsx    |   6 +-
 .../app/src/features/kb/detail/VectorKbDetail.tsx  | 659 +++++++++++++++++----
 .../app/src/features/office/OfficeEditorPage.tsx   |  79 +--
 packages/app/src/features/office/editorBridge.ts   |  12 +-
 6 files changed, 635 insertions(+), 161 deletions(-)
```

## 2026-05-01 11:52:52 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 89 +++++++++++++---------
 1 file changed, 52 insertions(+), 37 deletions(-)
```

## 2026-05-01 11:52:21 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/Shell.tsx`
- ` M packages/app/src/features/annotation/AnnotationPage.tsx`
- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/composer/KnowledgePickerPill.tsx`
- ` M packages/app/src/features/kb/detail/StructuredKbDetail.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`
- ` M packages/app/src/router/Chrome.tsx`
- ` M packages/app/src/store/loginModal.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           |  54 +++--
 packages/app/src/Shell.tsx                         |  61 ++++-
 .../app/src/features/annotation/AnnotationPage.tsx | 255 +++++++++++++++++++--
 .../app/src/features/composer/ChatComposer.tsx     |   3 +-
 .../src/features/composer/KnowledgePickerPill.tsx  |  24 +-
 .../src/features/kb/detail/StructuredKbDetail.tsx  |  29 ++-
 .../app/src/features/office/OfficeEditorPage.tsx   |  38 ++-
 packages/app/src/features/office/editorBridge.ts   |  31 ++-
 packages/app/src/router/Chrome.tsx                 |   2 -
 packages/app/src/store/loginModal.ts               |   5 +
 10 files changed, 444 insertions(+), 58 deletions(-)
```

## 2026-05-01 11:36:18 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/kb/KbAdminMenu.tsx`
- ` M packages/app/src/features/kb/detail/KbDetailHeader.tsx`
- ` M packages/app/src/features/kb/detail/KbDetailPage.tsx`
- ` M packages/app/src/features/kb/detail/StructuredKbDetail.tsx`

### Diff Stat

```text
 packages/app/src/features/kb/KbAdminMenu.tsx       |   2 +-
 .../app/src/features/kb/detail/KbDetailHeader.tsx  |  67 ++-
 .../app/src/features/kb/detail/KbDetailPage.tsx    |  10 +-
 .../src/features/kb/detail/StructuredKbDetail.tsx  | 511 ++++++++++++++-------
 4 files changed, 407 insertions(+), 183 deletions(-)
```

## 2026-05-01 11:32:11 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/kb/detail/KbDetailComposer.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- `?? packages/app/src/features/composer/KnowledgePickerPill.tsx`

### Diff Stat

```text
 .../app/src/features/composer/ChatComposer.tsx     |  11 ++
 .../src/features/kb/detail/KbDetailComposer.tsx    |  24 +++-
 .../app/src/features/office/OfficeEditorPage.tsx   | 155 +--------------------
 3 files changed, 38 insertions(+), 152 deletions(-)
```

## 2026-05-01 11:24:12 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js | 27 ++++++++++++++++++++-------
 1 file changed, 20 insertions(+), 7 deletions(-)
```

## 2026-05-01 11:21:24 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/api/src/annotation.ts`
- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/annotation/AnnotationPage.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           | 212 ++++++++++++++++-----
 packages/api/src/annotation.ts                     |  39 ++++
 packages/api/src/office.ts                         |  22 ++-
 .../app/src/features/annotation/AnnotationPage.tsx |  34 +++-
 .../app/src/features/office/OfficeEditorPage.tsx   | 138 ++++++++++++--
 packages/app/src/features/office/editorBridge.ts   |   7 +-
 6 files changed, 379 insertions(+), 73 deletions(-)
```

## 2026-05-01 11:07:32 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 29 ++++++++++++++--------
 1 file changed, 18 insertions(+), 11 deletions(-)
```

## 2026-05-01 11:06:11 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`

### Diff Stat

```text
 packages/api/src/endpoints.ts | 9 +++++++++
 1 file changed, 9 insertions(+)
```

## 2026-05-01 10:58:36 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/composer/ChatComposer.tsx     | 29 ++++++++++++++--------
 .../src/features/composer/ComposerModelPill.tsx    | 19 +++++++++++---
 .../app/src/features/office/OfficeEditorPage.tsx   |  2 ++
 3 files changed, 37 insertions(+), 13 deletions(-)
```

## 2026-05-01 10:55:09 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/annotation.ts`
- ` M packages/app/src/features/annotation/AnnotationPage.tsx`

### Diff Stat

```text
 packages/api/src/annotation.ts                     |   1 +
 .../app/src/features/annotation/AnnotationPage.tsx | 190 +++++++++++++++++++--
 2 files changed, 177 insertions(+), 14 deletions(-)
```

## 2026-05-01 10:52:39 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/kb/detail/DocumentKbDetail.tsx`
- ` M packages/app/src/features/kb/detail/KbDetailPage.tsx`

### Diff Stat

```text
 .../src/features/kb/detail/DocumentKbDetail.tsx    | 22 ++++++++++++++++++++--
 .../app/src/features/kb/detail/KbDetailPage.tsx    |  7 ++++++-
 2 files changed, 26 insertions(+), 3 deletions(-)
```

## 2026-05-01 10:47:18 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/MessageBubble.tsx`

### Diff Stat

```text
 packages/app/src/features/chat/MessageBubble.tsx | 42 +++++++++++-------------
 1 file changed, 20 insertions(+), 22 deletions(-)
```

## 2026-05-01 10:38:45 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/web/vite.config.ts`

### Diff Stat

```text
 apps/web/vite.config.ts | 1 +
 1 file changed, 1 insertion(+)
```

## 2026-05-01 10:36:24 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/kbUniverse.ts`
- ` M packages/app/src/features/kb/detail/KbDetailPage.tsx`
- ` M packages/app/src/features/kb/detail/StructuredKbDetail.tsx`
- ` M packages/app/src/features/kb/shared/useKbAskTrial.ts`

### Diff Stat

```text
 packages/api/src/kbUniverse.ts                     |   6 +
 .../app/src/features/kb/detail/KbDetailPage.tsx    | 129 +++++++++++++++++++--
 .../src/features/kb/detail/StructuredKbDetail.tsx  |  64 +++++++++-
 .../app/src/features/kb/shared/useKbAskTrial.ts    |   9 +-
 4 files changed, 191 insertions(+), 17 deletions(-)
```

## 2026-05-01 10:32:07 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/app/src/features/kb/detail/DocumentKbDetail.tsx`
- ` M packages/app/src/features/kb/upload/KbDropZone.tsx`
- ` M packages/app/src/features/kb/upload/useKbUpload.ts`
- `?? packages/app/src/features/kb/upload/duplicateCheck.ts`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      |  8 +--
 .../src/features/kb/detail/DocumentKbDetail.tsx    | 78 ++++++----------------
 packages/app/src/features/kb/upload/KbDropZone.tsx | 26 +++++++-
 packages/app/src/features/kb/upload/useKbUpload.ts |  7 +-
 4 files changed, 50 insertions(+), 69 deletions(-)
```

## 2026-05-01 10:25:44 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/app/src/features/chat/MessageBubble.tsx`
- ` M packages/app/src/features/kb/detail/DocumentKbDetail.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      | 21 +++++-
 packages/app/src/features/chat/MessageBubble.tsx   | 36 +++++----
 .../src/features/kb/detail/DocumentKbDetail.tsx    | 87 ++++++++++++++++++++--
 3 files changed, 122 insertions(+), 22 deletions(-)
```

## 2026-05-01 10:05:09 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/annotation.ts`
- ` M packages/app/src/features/annotation/AnnotationPage.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/api/src/annotation.ts                     | 21 +++++++
 .../app/src/features/annotation/AnnotationPage.tsx | 68 +++++++++++++++++++++-
 .../app/src/features/office/OfficeEditorPage.tsx   |  7 ++-
 3 files changed, 92 insertions(+), 4 deletions(-)
```

## 2026-05-01 09:49:29 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 63 +++++++++++++++++++---
 1 file changed, 56 insertions(+), 7 deletions(-)
```

## 2026-05-01 09:47:47 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/ui/src/components/DropdownMenu.tsx`

### Diff Stat

```text
 packages/api/src/office.ts                         |   1 +
 .../app/src/features/composer/ChatComposer.tsx     |  22 ++-
 .../src/features/composer/ComposerModelPill.tsx    |  21 ++-
 .../app/src/features/office/OfficeEditorPage.tsx   | 174 ++++++++++++++++++++-
 packages/ui/src/components/DropdownMenu.tsx        |   2 +-
 5 files changed, 210 insertions(+), 10 deletions(-)
```

## 2026-05-01 09:28:00 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M onlyoffice-plugin/chayuan-office/code.js`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 onlyoffice-plugin/chayuan-office/code.js           |  31 +++++
 .../app/src/features/office/OfficeEditorPage.tsx   |  78 +++++++------
 packages/app/src/features/office/editorBridge.ts   | 127 ++++++++++++++++-----
 3 files changed, 168 insertions(+), 68 deletions(-)
```

## 2026-05-01 09:14:57 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/chat/MessageBubble.tsx`
- ` M packages/app/src/features/chat/useChayuanChat.ts`
- ` M packages/app/src/features/kb/KbAskResultPanel.tsx`
- ` M packages/app/src/features/kb/KbBoard.tsx`
- ` M packages/transport/src/sse-parser.ts`
- ` M packages/transport/src/types.ts`

### Diff Stat

```text
 packages/app/src/features/chat/MessageBubble.tsx  | 175 ++++++++++++++++------
 packages/app/src/features/chat/useChayuanChat.ts  |   9 ++
 packages/app/src/features/kb/KbAskResultPanel.tsx |  28 +++-
 packages/app/src/features/kb/KbBoard.tsx          |  12 +-
 packages/transport/src/sse-parser.ts              |  69 ++++++---
 packages/transport/src/types.ts                   |   9 ++
 6 files changed, 231 insertions(+), 71 deletions(-)
```

## 2026-05-01 09:04:15 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 packages/app/src/features/office/OfficeEditorPage.tsx | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

## 2026-05-01 08:46:25 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/web/vite.config.ts`
- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/office/OfficePage.tsx`
- ` M packages/app/src/features/office/components/DocumentCard.tsx`
- ` M packages/app/src/features/office/components/VectorStatusBadge.tsx`
- ` M packages/app/src/features/office/hooks/useVectorizeStream.ts`
- ` M packages/app/src/features/office/types.ts`

### Diff Stat

```text
 apps/web/vite.config.ts                            |  6 +++
 packages/api/src/office.ts                         | 38 +++++++++++++++-
 packages/app/src/features/office/OfficePage.tsx    | 50 ++++++++++++++++++++++
 .../features/office/components/DocumentCard.tsx    |  5 +++
 .../office/components/VectorStatusBadge.tsx        | 31 +++++++++++++-
 .../features/office/hooks/useVectorizeStream.ts    |  5 +++
 packages/app/src/features/office/types.ts          | 10 +++++
 7 files changed, 141 insertions(+), 4 deletions(-)
```

## 2026-05-01 08:32:59 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 142 +++++++++++++++++----
 1 file changed, 120 insertions(+), 22 deletions(-)
```

## 2026-05-01 08:23:33 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/office/components/DocumentCard.tsx`
- ` M packages/app/src/features/office/components/VectorStatusBadge.tsx`
- ` M packages/app/src/features/office/hooks/useVectorizeStream.ts`
- ` M packages/app/src/features/office/types.ts`

### Diff Stat

```text
 packages/api/src/office.ts                         |  5 ++++
 .../features/office/components/DocumentCard.tsx    |  2 ++
 .../office/components/VectorStatusBadge.tsx        | 34 ++++++++++++++++++----
 .../features/office/hooks/useVectorizeStream.ts    | 14 ++++++++-
 packages/app/src/features/office/types.ts          | 17 +++++++++++
 5 files changed, 66 insertions(+), 6 deletions(-)
```

## 2026-05-01 08:16:44 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M apps/web/vite.config.ts`

### Diff Stat

```text
 apps/web/vite.config.ts | 2 ++
 1 file changed, 2 insertions(+)
```

## 2026-05-01 08:07:02 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/app/src/features/kb/KbBoard.tsx`
- ` M packages/app/src/features/kb/detail/DocumentKbDetail.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      | 23 +++++++++
 packages/app/src/features/kb/KbBoard.tsx           | 31 +-----------
 .../src/features/kb/detail/DocumentKbDetail.tsx    | 58 ++++++++++++++++++++++
 3 files changed, 82 insertions(+), 30 deletions(-)
```

## 2026-05-01 08:01:03 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/app/src/features/kb/detail/DocumentKbDetail.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      |  16 +++
 .../src/features/kb/detail/DocumentKbDetail.tsx    | 137 ++++++++++++++++++++-
 2 files changed, 152 insertions(+), 1 deletion(-)
```

## 2026-05-01 07:52:35 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/kb/KbBoard.tsx`

### Diff Stat

```text
 packages/api/src/office.ts               |  14 +-
 packages/app/src/features/kb/KbBoard.tsx | 255 ++++++++++++++++++++-----------
 2 files changed, 174 insertions(+), 95 deletions(-)
```

## 2026-05-01 07:47:40 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/api/src/kbUniverse.ts`
- ` M packages/app/src/features/kb/detail/DocumentKbDetail.tsx`
- ` M packages/app/src/features/kb/detail/KbDetailPage.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      | 10 +++++
 packages/api/src/kbUniverse.ts                     |  3 ++
 .../src/features/kb/detail/DocumentKbDetail.tsx    | 50 +++++++++++++++++++++-
 .../app/src/features/kb/detail/KbDetailPage.tsx    |  7 +--
 4 files changed, 65 insertions(+), 5 deletions(-)
```

## 2026-05-01 07:37:33 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/kb/create/DocumentKbForm.tsx`
- ` M packages/app/src/features/kb/create/ImageKbForm.tsx`
- ` M packages/app/src/features/kb/create/VectorKbForm.tsx`

### Diff Stat

```text
 packages/app/src/features/kb/create/DocumentKbForm.tsx | 15 ++++++++++-----
 packages/app/src/features/kb/create/ImageKbForm.tsx    | 13 +++++++++----
 packages/app/src/features/kb/create/VectorKbForm.tsx   | 11 ++++++++---
 3 files changed, 27 insertions(+), 12 deletions(-)
```

## 2026-05-01 07:33:48 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/kbUniverse.ts`
- ` M packages/app/src/features/kb/KbAdminMenu.tsx`
- ` M packages/app/src/features/kb/KbAskResultPanel.tsx`
- ` M packages/app/src/features/kb/KbBoard.tsx`
- ` M packages/app/src/features/office/OfficePage.tsx`
- ` M packages/app/src/store/kbBoardPrefs.ts`

### Diff Stat

```text
 packages/api/src/kbUniverse.ts                    |   4 +
 packages/app/src/features/kb/KbAdminMenu.tsx      |   2 +-
 packages/app/src/features/kb/KbAskResultPanel.tsx |  54 ++-
 packages/app/src/features/kb/KbBoard.tsx          | 388 ++++++++++++++++++++--
 packages/app/src/features/office/OfficePage.tsx   |  62 +++-
 packages/app/src/store/kbBoardPrefs.ts            |  46 ++-
 6 files changed, 524 insertions(+), 32 deletions(-)
```

## 2026-05-01 06:47:42 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/kb/KbAskResultPanel.tsx`
- ` M packages/app/src/features/office/OfficePage.tsx`
- ` M packages/app/src/features/office/components/DocumentCard.tsx`
- ` M packages/app/src/features/office/components/GroupCard.tsx`
- ` M packages/app/src/features/office/components/LibraryGrid.tsx`
- ` M packages/app/src/features/office/components/NewDocCardGroup.tsx`
- ` M packages/app/src/features/office/store/officeUI.ts`

### Diff Stat

```text
 packages/app/src/features/kb/KbAskResultPanel.tsx  |  47 ++++--
 packages/app/src/features/office/OfficePage.tsx    | 172 +++++++++++++++++++--
 .../features/office/components/DocumentCard.tsx    |   4 +-
 .../src/features/office/components/GroupCard.tsx   |   4 +-
 .../src/features/office/components/LibraryGrid.tsx |   4 +-
 .../features/office/components/NewDocCardGroup.tsx |   4 +-
 packages/app/src/features/office/store/officeUI.ts |  12 +-
 7 files changed, 211 insertions(+), 36 deletions(-)
```

## 2026-05-01 06:28:56 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 63 +++++++++++++++-------
 1 file changed, 44 insertions(+), 19 deletions(-)
```

## 2026-05-01 06:20:16 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/office/OfficeEditorPage.tsx`

### Diff Stat

```text
 .../app/src/features/office/OfficeEditorPage.tsx   | 307 +++++++++++++++------
 1 file changed, 223 insertions(+), 84 deletions(-)
```

## 2026-05-01 05:59:49 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/app/src/features/chat/ModelPicker.tsx`
- ` M packages/app/src/features/composer/ComposerModelPill.tsx`
- ` M packages/app/src/features/composer/ModelMenuList.tsx`

### Diff Stat

```text
 packages/api/src/endpoints.ts                           | 17 +++++++++++++++--
 packages/app/src/features/chat/ModelPicker.tsx          |  5 +++--
 .../app/src/features/composer/ComposerModelPill.tsx     |  5 +++--
 packages/app/src/features/composer/ModelMenuList.tsx    | 17 ++++++++++++++---
 4 files changed, 35 insertions(+), 9 deletions(-)
```

## 2026-05-01 05:49:13 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/shell/TabHost.tsx`

### Diff Stat

```text
 packages/app/src/features/shell/TabHost.tsx | 37 +++++++++++++++++++++++++----
 1 file changed, 33 insertions(+), 4 deletions(-)
```

## 2026-05-01 05:39:27 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/index.ts`
- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/kb/KbAskResultPanel.tsx`
- ` M packages/app/src/features/kb/KbBoard.tsx`
- ` M packages/app/src/features/kb/detail/KbDetailComposer.tsx`
- ` M packages/app/src/features/kb/shared/useKbAskTrial.ts`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/OfficePage.tsx`
- ` M packages/app/src/features/office/editorBridge.ts`
- ` M packages/app/src/features/placeholders/SettingsAsPage.tsx`
- ` M packages/app/src/features/shell/Sidebar.tsx`
- ` M packages/app/src/features/shell/TabHost.tsx`
- ` M packages/app/src/features/shell/page-registry.tsx`
- ` M packages/app/src/router/index.tsx`
- `?? onlyoffice-plugin/chayuan-office/README.md`
- `?? onlyoffice-plugin/chayuan-office/code.js`
- `?? onlyoffice-plugin/chayuan-office/config.json`
- `?? onlyoffice-plugin/chayuan-office/index.html`
- `?? packages/api/src/annotation.ts`
- `?? packages/app/src/features/annotation/AnnotationPage.tsx`

### Diff Stat

```text
 packages/api/src/index.ts                          |   1 +
 packages/api/src/office.ts                         | 188 ++++++++++++++++++-
 packages/app/src/features/kb/KbAskResultPanel.tsx  | 132 +++++++++++++-
 packages/app/src/features/kb/KbBoard.tsx           |  22 ++-
 .../src/features/kb/detail/KbDetailComposer.tsx    |  17 +-
 .../app/src/features/kb/shared/useKbAskTrial.ts    |  11 +-
 .../app/src/features/office/OfficeEditorPage.tsx   | 197 ++++++++++++++++++--
 packages/app/src/features/office/OfficePage.tsx    | 200 ++++++++++++++++++++-
 packages/app/src/features/office/editorBridge.ts   | 163 ++++++++++++++++-
 .../src/features/placeholders/SettingsAsPage.tsx   |  53 +++++-
 packages/app/src/features/shell/Sidebar.tsx        |   3 +
 packages/app/src/features/shell/TabHost.tsx        |   9 +-
 packages/app/src/features/shell/page-registry.tsx  |  14 +-
 packages/app/src/router/index.tsx                  |   1 +
 14 files changed, 972 insertions(+), 39 deletions(-)
```

## 2026-04-30 22:39:30 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/office/OfficePage.tsx`
- ` M packages/app/src/features/office/components/SelectionToolbar.tsx`

### Diff Stat

```text
 packages/api/src/office.ts                         | 33 +++++++++++
 packages/app/src/features/office/OfficePage.tsx    | 68 ++++++++++++++++++++++
 .../office/components/SelectionToolbar.tsx         | 11 +++-
 3 files changed, 110 insertions(+), 2 deletions(-)
```

## 2026-04-30 22:33:18 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/api/src/endpoints.ts`
- ` M packages/api/src/office.ts`
- ` M packages/app/src/features/kb/detail/DocumentKbDetail.tsx`
- ` M packages/app/src/features/office/OfficeEditorPage.tsx`
- ` M packages/app/src/features/office/OfficePage.tsx`
- ` M packages/app/src/features/office/store/officeChat.ts`
- `?? packages/app/src/features/office/editorBridge.ts`

### Diff Stat

```text
 packages/api/src/endpoints.ts                      |  26 ++
 packages/api/src/office.ts                         |  97 ++++
 .../src/features/kb/detail/DocumentKbDetail.tsx    | 144 +++++-
 .../app/src/features/office/OfficeEditorPage.tsx   | 490 +++++++++++++++++----
 packages/app/src/features/office/OfficePage.tsx    | 187 ++++++--
 .../app/src/features/office/store/officeChat.ts    |  79 +++-
 6 files changed, 888 insertions(+), 135 deletions(-)
```

## 2026-04-30 20:39:20 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/features/composer/ChatComposer.tsx`
- ` D packages/app/src/features/composer/KnowledgeBasePickerPill.tsx`
- ` M packages/app/src/features/kb/KbBoard.tsx`
- ` M packages/app/src/features/kb/detail/KbDetailComposer.tsx`

### Diff Stat

```text
 .../app/src/features/composer/ChatComposer.tsx     |  5 --
 .../features/composer/KnowledgeBasePickerPill.tsx  | 54 ----------------------
 packages/app/src/features/kb/KbBoard.tsx           |  1 -
 .../src/features/kb/detail/KbDetailComposer.tsx    |  1 -
 4 files changed, 61 deletions(-)
```

## 2026-04-30 20:13:35 +0800 - chayuan-client

- Branch: `main`
- Summary: auto-recorded local changes before commit.

### Changed Files

- ` M packages/app/src/router/index.tsx`

### Diff Stat

```text
 packages/app/src/router/index.tsx | 41 ++++++++++++++++++++++++++-------------
 1 file changed, 27 insertions(+), 14 deletions(-)
```

