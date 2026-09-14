# 24시간 서버 실행

휴대폰 앱이 닫혀 있어도 10분 모니터링을 계속하려면 Python 프로세스가 24시간 켜진 PC/VPS에서 실행되어야 합니다.

## Ubuntu/VPS

1. ZIP을 서버에 풀고 `.env.example`을 `.env`로 복사합니다.
2. KIS/DART/Telegram 키를 `.env`에 입력합니다.
3. 외부 접속을 허용한다면 `APP_API_TOKEN`에 긴 랜덤 문자열을 반드시 설정합니다.
4. 실행:

```bash
chmod +x install_ubuntu_service.sh
./install_ubuntu_service.sh
```

상태:

```bash
sudo systemctl status krx-ai-mobile
```

로그:

```bash
journalctl -u krx-ai-mobile -f
```

중지:

```bash
sudo systemctl disable --now krx-ai-mobile
```

## HTTPS

공용 인터넷에서 아이폰으로 접속하려면 HTTPS reverse proxy 또는 VPN/Tailscale 같은 사설 네트워크를 권장합니다. 비밀키는 서버 `.env`에만 두고 웹앱에 저장하지 않습니다. `APP_API_TOKEN`은 서버 API 접근을 제한하는 용도입니다.
