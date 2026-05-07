# STAB AutoAdd — 베타 테스트

우동배 게스트 자동 등록 시스템입니다.  
카카오톡 투표 사진에서 참가자를 자동 인식해 nearminton에 등록합니다.

---

## 사전 준비

1. **Python 3.10 이상** 설치 ([python.org](https://www.python.org/downloads/))
   - Windows: 설치 시 **"Add Python to PATH"** 반드시 체크
2. **Google Chrome** 설치
3. **Naver CLOVA OCR API 키** 발급 (별도 안내 참고)

---

## 설치

### Windows
```
install.bat 더블클릭
```

### Mac
```bash
chmod +x install.sh
./install.sh
```

---

## API 키 설정

`site_config.json` 파일을 열어 CLOVA OCR 정보를 입력하세요:

```json
{
  "clova_invoke_url": "여기에_CLOVA_OCR_Invoke_URL_입력",
  "clova_secret_key": "여기에_CLOVA_OCR_Secret_Key_입력"
}
```

---

## 회원 정보 설정

`members.csv`를 실제 회원 명단으로 교체하세요.

| 컬럼 | 설명 | 예시 |
|------|------|------|
| member_id | 고유 번호 | 1 |
| name | 이름 (동명이인은 `홍길동(23)` 형식) | 홍길동(23) |
| gender | 성별 | man / woman |
| level | 급수 | D, C, B, A, S, E, F, N |
| aliases | 추가 이름(별칭, `\|` 구분) | 길동\|홍길동 |

---

## 실행

### Windows
```
run.bat 더블클릭
```

### Mac
```bash
./run.sh
```

브라우저에서 `http://localhost:포트번호` 가 자동으로 열립니다.

---

## 사용 방법

1. **투표 사진** → `vote/` 폴더에 넣기
2. **실행 버튼** 클릭
3. Chrome 탭이 열리면 nearminton에 로그인
4. Chrome 탭의 **▶ 계속 진행** 버튼 클릭
5. 자동으로 참가자 등록 완료

---

## 문제 신고

베타 테스트 중 발생한 오류는 이슈로 등록해주세요.
