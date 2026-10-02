# 확정 구현 순서

1. **Jev–정책–Windows Host 통합 runner** — 완료
2. **Workflow JSON 로더·스키마·로컬 action 결속** — 완료
3. **영상 업로드·Gemini 추출 API** — 다음 단계
4. **추출 결과 검토·수정·승인 UI**
5. **제한된 실제 업무 connector**
6. **데모 관측성·패키징·배포 마감**

각 단계는 앞 단계의 안전 계약을 우회하지 않는다. 모델 출력은 Workflow 초안일 뿐이며, 실행 가능한 action payload는 로컬 registry에서만 가져온다.
